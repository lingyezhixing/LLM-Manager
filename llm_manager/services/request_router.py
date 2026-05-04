from __future__ import annotations

import logging
import time

import httpx

from llm_manager.schemas.model import ModelState
from llm_manager.plugins.registry import PluginRegistry
from llm_manager.services.base import BaseService
from llm_manager.container import Container
from llm_manager.services.model_manager import ModelManager

logger = logging.getLogger(__name__)


class RequestRouter(BaseService):
    def __init__(self, container: Container):
        super().__init__(container)
        self._client: httpx.AsyncClient | None = None
        self._model_manager: ModelManager | None = None
        self._plugin_registry: PluginRegistry | None = None

    async def on_start(self) -> None:
        self._model_manager = self._container.resolve(ModelManager)
        self._plugin_registry = self._container.resolve(PluginRegistry)
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

        instance = self._model_manager.get_instance(resolved)
        if instance is None or instance.state != ModelState.RUNNING:
            raise RuntimeError(f"Model '{resolved}' is not running")

        self._validate_path(resolved, instance, path)

        url = f"http://127.0.0.1:{instance.config.port}{path}"

        if method.upper() == "POST":
            response = await self._client.post(url, json=body, headers=headers)
        elif method.upper() == "GET":
            response = await self._client.get(url, headers=headers)
        else:
            response = await self._client.request(method, url, json=body, headers=headers)

        self._track_request(resolved, instance, response)

        return response

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

        instance = self._model_manager.get_instance(resolved)
        if instance is None or instance.state != ModelState.RUNNING:
            raise RuntimeError(f"Model '{resolved}' is not running")

        self._validate_path(resolved, instance, path)

        url = f"http://127.0.0.1:{instance.config.port}{path}"

        async with self._client.stream("POST", url, json=body, headers=headers) as response:
            yield response

    def _track_request(self, model_name: str, instance, response: httpx.Response):
        instance.last_request_at = time.time()
        logger.debug(
            "Request to '%s': status=%d",
            model_name,
            response.status_code,
        )
