from __future__ import annotations

import logging

import httpx

from llm_manager.schemas.request import TokenUsage
from llm_manager.plugins.base_interface import InterfacePlugin

logger = logging.getLogger(__name__)


class RerankerInterface(InterfacePlugin):
    name = "reranker"

    def get_supported_endpoints(self) -> list[str]:
        return ["/v1/rerank", "/rerank"]

    async def health_check(self, port: int, timeout: float = 300.0) -> bool:
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"http://127.0.0.1:{port}/v1/models",
                    timeout=3.0,
                )
                if resp.status_code != 200:
                    return False
        except Exception:
            return False

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.post(
                    f"http://127.0.0.1:{port}/rerank",
                    json={
                        "model": "health-check",
                        "query": "test",
                        "documents": ["test"],
                    },
                    timeout=timeout,
                )
                return resp.status_code == 200
        except Exception:
            return False

    def extract_token_usage(self, response: dict) -> TokenUsage:
        usage = response.get("usage", {})
        return TokenUsage(
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=0,
            total_tokens=usage.get("total_tokens", 0),
        )
