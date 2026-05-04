from __future__ import annotations

import asyncio
import logging
import time

import httpx

from llm_manager.plugins.base_interface import InterfacePlugin

logger = logging.getLogger(__name__)


class RerankerInterface(InterfacePlugin):
    name = "reranker"

    def get_supported_endpoints(self) -> list[str]:
        return ["/v1/rerank", "/rerank"]

    async def health_check(
        self,
        port: int,
        model_name: str,
        start_time: float,
        timeout_seconds: float = 300.0,
    ) -> bool:
        base = f"http://127.0.0.1:{port}"

        # 第一阶段：浅层检查
        while time.time() - start_time < timeout_seconds:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.get(f"{base}/v1/models", timeout=3.0)
                    if resp.status_code == 200:
                        break
            except Exception:
                pass
            await asyncio.sleep(2)
        else:
            return False

        # 第二阶段：深层检查
        while time.time() - start_time < timeout_seconds:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"{base}/rerank",
                        json={
                            "model": model_name,
                            "query": "test",
                            "documents": ["test"],
                        },
                        timeout=5.0,
                    )
                    if resp.status_code == 200:
                        return True
            except Exception:
                pass
            await asyncio.sleep(1)

        return False

    def validate_request(self, path: str, model_name: str) -> tuple[bool, str]:
        if "/v1/rerank" not in path and "/rerank" not in path:
            return False, f"模型 '{model_name}' 是 'Reranker' 模式, 只支持重排序接口"
        return True, ""
