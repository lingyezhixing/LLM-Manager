from __future__ import annotations

import logging

import httpx

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

    def validate_request(self, path: str, model_name: str) -> tuple[bool, str]:
        if "/v1/rerank" not in path and "/rerank" not in path:
            return False, f"模型 '{model_name}' 是 'Reranker' 模式, 只支持重排序接口"
        return True, ""
