from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import AsyncIterator

import httpx

from llm_manager.schemas.model import ModelState
from llm_manager.plugins.registry import PluginRegistry
from llm_manager.services.base import BaseService
from llm_manager.container import Container
from llm_manager.services.model_manager import ModelManager
from llm_manager.services.token_tracker import TokenTracker

logger = logging.getLogger(__name__)

_HEADERS_TO_STRIP = {"host", "content-length", "transfer-encoding", "content-type"}


@dataclass
class RouteResult:
    is_streaming: bool = False
    status_code: int = 200
    content: bytes = b""
    response_headers: dict = None
    stream: AsyncIterator[bytes] = None

    def __post_init__(self):
        if self.response_headers is None:
            self.response_headers = {}


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

    def get_pending_count(self, name: str) -> int:
        return self._pending.get(name, 0)

    def _validate_path(self, resolved: str, instance, path: str) -> None:
        interface = self._plugin_registry.get_interface(instance.config.mode)
        if interface:
            is_valid, error_msg = interface.validate_request(path, resolved)
            if not is_valid:
                raise ValueError(error_msg)

    def _clean_headers(self, headers: dict) -> dict:
        return {k: v for k, v in headers.items() if k.lower() not in _HEADERS_TO_STRIP}

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

            is_starting = instance.state in (ModelState.STARTING, ModelState.HEALTH_CHECK)
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
        raw_body: bytes | None = None,
        request_headers: dict | None = None,
    ) -> RouteResult:
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
            headers = self._clean_headers(request_headers) if request_headers else {}
            content = raw_body if raw_body is not None else (body if body is not None else None)

            req = self._client.build_request(
                method=method.upper(),
                url=url,
                json=content if isinstance(content, dict) else None,
                content=content if isinstance(content, bytes) else None,
                headers=headers,
            )

            response = await self._client.send(req, stream=True)
            is_streaming = "text/event-stream" in response.headers.get("content-type", "")

            if is_streaming:
                token_stream = self._token_tracker.wrap_streaming_response(
                    resolved, response, start_time, instance.config.mode,
                )
                return RouteResult(
                    is_streaming=True,
                    status_code=response.status_code,
                    response_headers=dict(response.headers),
                    stream=token_stream,
                )
            else:
                resp_content = await response.aread()
                resp_status = response.status_code
                resp_headers = dict(response.headers)
                await response.aclose()

                try:
                    import json as _json
                    data = _json.loads(resp_content) if resp_content else None
                    if data and resp_status == 200:
                        usage = self._token_tracker.extract_tokens(data)
                        await self._token_tracker.record_request(
                            resolved, usage, start_time, time.time(), instance.config.mode,
                        )
                except Exception:
                    pass

                return RouteResult(
                    is_streaming=False,
                    status_code=resp_status,
                    content=resp_content,
                    response_headers=resp_headers,
                )

        except ValueError as e:
            raise e
        except RuntimeError as e:
            raise e
        except Exception as e:
            logger.exception("Request routing failed for model '%s'", resolved)
            raise RuntimeError(str(e)) from e
        finally:
            self._decrement_pending(resolved)
