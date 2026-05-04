from __future__ import annotations

import logging
import time

import httpx

from llm_manager.schemas.model import ModelState
from llm_manager.schemas.request import TokenUsage
from llm_manager.plugins.registry import PluginRegistry
from llm_manager.services.base import BaseService
from llm_manager.container import Container
from llm_manager.services.model_manager import ModelManager

logger = logging.getLogger(__name__)


class RequestRouter(BaseService):
    def __init__(self, container: Container):
        super().__init__(container)

    async def route_request(
        self,
        model_name_or_alias: str,
        path: str,
        method: str,
        body: dict | None = None,
        headers: dict | None = None,
    ) -> httpx.Response:
        model_svc = self._container.resolve(ModelManager)
        resolved = model_svc.resolve_model_name(model_name_or_alias)
        if resolved is None:
            raise ValueError(f"Model '{model_name_or_alias}' not found")

        instance = model_svc.get_instance(resolved)
        if instance is None or instance.state != ModelState.RUNNING:
            raise RuntimeError(f"Model '{resolved}' is not running")

        url = f"http://127.0.0.1:{instance.config.port}{path}"

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            if method.upper() == "POST":
                response = await client.post(url, json=body, headers=headers)
            elif method.upper() == "GET":
                response = await client.get(url, headers=headers)
            else:
                response = await client.request(method, url, json=body, headers=headers)

        self._track_request(resolved, instance, response)

        return response

    async def route_streaming(
        self,
        model_name_or_alias: str,
        path: str,
        body: dict | None = None,
        headers: dict | None = None,
    ):
        model_svc = self._container.resolve(ModelManager)
        resolved = model_svc.resolve_model_name(model_name_or_alias)
        if resolved is None:
            raise ValueError(f"Model '{model_name_or_alias}' not found")

        instance = model_svc.get_instance(resolved)
        if instance is None or instance.state != ModelState.RUNNING:
            raise RuntimeError(f"Model '{resolved}' is not running")

        url = f"http://127.0.0.1:{instance.config.port}{path}"

        async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
            async with client.stream("POST", url, json=body, headers=headers) as response:
                yield response

    def _track_request(self, model_name: str, instance, response: httpx.Response):
        instance.last_request_at = time.time()

        try:
            plugin_registry = self._container.resolve(PluginRegistry)
            interface = plugin_registry.get_interface(instance.config.mode)
            if interface and response.status_code == 200:
                try:
                    data = response.json()
                    usage = interface.extract_token_usage(data)
                except Exception:
                    usage = TokenUsage()
            else:
                usage = TokenUsage()
        except Exception:
            usage = TokenUsage()

        logger.debug(
            "Request to '%s': %d tokens (%d+%d), status=%d",
            model_name,
            usage.total_tokens,
            usage.prompt_tokens,
            usage.completion_tokens,
            response.status_code,
        )
