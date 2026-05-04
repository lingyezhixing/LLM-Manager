from __future__ import annotations

import logging

import httpx

from llm_manager.plugins.base_interface import InterfacePlugin

logger = logging.getLogger(__name__)


class EmbeddingInterface(InterfacePlugin):
    name = "embedding"

    def get_supported_endpoints(self) -> list[str]:
        return ["/v1/embeddings"]

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
                    f"http://127.0.0.1:{port}/v1/embeddings",
                    json={
                        "model": "health-check",
                        "input": "test",
                    },
                    timeout=timeout,
                )
                return resp.status_code == 200
        except Exception:
            return False

    def validate_request(self, path: str, model_name: str) -> tuple[bool, str]:
        if "/v1/chat/completions" in path or "/v1/completions" in path:
            return False, f"模型 '{model_name}' 是 'Embedding' 模式, 不支持聊天或文本补全接口"
        return True, ""
