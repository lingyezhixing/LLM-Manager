from __future__ import annotations

import asyncio
import logging
import time

import httpx

from llm_manager.schemas.model import ModelState
from llm_manager.plugins.registry import PluginRegistry
from llm_manager.services.base import BaseService
from llm_manager.container import Container
from llm_manager.services.model_manager import ModelManager
from llm_manager.services.token_tracker import TokenTracker

logger = logging.getLogger(__name__)


class RequestRouter(BaseService):
    def __init__(self, container: Container):
        super().__init__(container)
        self._client: httpx.AsyncClient | None = None
        self._model_manager: ModelManager | None = None
        self._plugin_registry: PluginRegistry | None = None
        self._token_tracker: TokenTracker | None = None
        self._pending: dict[str, int] = {}
        self._starting_models: set[str] = set()

    async def on_start(self) -> None:
        self._model_manager = self._container.resolve(ModelManager)
        self._plugin_registry = self._container.resolve(PluginRegistry)
        self._token_tracker = self._container.resolve(TokenTracker)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(300.0))

    async def on_stop(self) -> None:
        if self._client:
            await self._client.aclose()
            self._client = None

    def _validate_path(self, resolved: str, instance, path: str) -> None:
        interface = self._plugin_registry.get_interface(instance.config.mode)
        if interface:
            is_valid, error_msg = interface.validate_request(path, resolved)
            if not is_valid:
                raise ValueError(error_msg)

    def _increment_pending(self, name: str) -> None:
        self._pending[name] = self._pending.get(name, 0) + 1
        instance = self._model_manager.get_instance(name)
        if instance:
            instance.last_request_at = time.time()

    def _decrement_pending(self, name: str) -> None:
        if name in self._pending:
            self._pending[name] = max(0, self._pending[name] - 1)

    async def _ensure_model_running(self, resolved: str) -> None:
        """智能启动控制：确保模型处于 RUNNING 状态"""
        while True:
            instance = self._model_manager.get_instance(resolved)
            if instance is None:
                raise ValueError(f"Model '{resolved}' not found")

            if instance.state == ModelState.RUNNING:
                return

            is_starting = instance.state == ModelState.STARTING
            is_starting_local = resolved in self._starting_models

            if is_starting or is_starting_local:
                await asyncio.sleep(0.5)
                continue

            if instance.state in (ModelState.STOPPED, ModelState.FAILED):
                self._starting_models.add(resolved)
                try:
                    await self._model_manager.start_model(resolved)
                finally:
                    self._starting_models.discard(resolved)
                continue

            await asyncio.sleep(0.5)

    async def route_request(
        self,
        model_name_or_alias: str,
        path: str,
        method: str,
        body: dict | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        resolved = self._model_manager.resolve_model_name(model_name_or_alias)
        if resolved is None:
            raise ValueError(f"Model '{model_name_or_alias}' not found")

        self._increment_pending(resolved)
        start_time = time.time()

        try:
            await self._ensure_model_running(resolved)
            instance = self._model_manager.get_instance(resolved)
            self._validate_path(resolved, instance, path)

            url = f"http://127.0.0.1:{instance.config.port}{path}"

            if method.upper() == "POST":
                response = await self._client.post(url, json=body, headers=headers)
            elif method.upper() == "GET":
                response = await self._client.get(url, headers=headers)
            else:
                response = await self._client.request(method, url, json=body, headers=headers)

            if response.status_code == 200:
                try:
                    data = response.json()
                    usage = self._token_tracker.extract_tokens(data)
                    await self._token_tracker.record_request(
                        resolved, usage, start_time, time.time(), instance.config.mode,
                    )
                except Exception:
                    logger.debug("Token extraction failed for non-streaming response")

            return response
        finally:
            self._decrement_pending(resolved)

    async def route_streaming(
        self,
        model_name_or_alias: str,
        path: str,
        body: dict | None = None,
        headers: dict | None = None,
    ):
        resolved = self._model_manager.resolve_model_name(model_name_or_alias)
        if resolved is None:
            raise ValueError(f"Model '{model_name_or_alias}' not found")

        self._increment_pending(resolved)
        start_time = time.time()

        try:
            await self._ensure_model_running(resolved)
            instance = self._model_manager.get_instance(resolved)
            self._validate_path(resolved, instance, path)

            url = f"http://127.0.0.1:{instance.config.port}{path}"

            async with self._client.stream("POST", url, json=body, headers=headers) as response:
                token_stream = self._token_tracker.wrap_streaming_response(
                    resolved, response, start_time, instance.config.mode,
                )
                async for chunk in token_stream:
                    yield chunk
        finally:
            self._decrement_pending(resolved)
