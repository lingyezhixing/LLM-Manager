from __future__ import annotations

import asyncio
import logging
import time

import httpx

from llm_manager.plugins.base_interface import InterfacePlugin

logger = logging.getLogger(__name__)


class ChatInterface(InterfacePlugin):
    name = "chat"

    def get_supported_endpoints(self) -> list[str]:
        return ["/v1/chat/completions", "/v1/completions"]

    async def health_check(
        self,
        port: int,
        model_name: str,
        start_time: float,
        timeout_seconds: float = 300.0,
    ) -> bool:
        base = f"http://127.0.0.1:{port}"

        # 第一阶段：浅层检查 - 验证服务是否可用
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

        # 第二阶段：深层检查 - 验证聊天接口功能
        while time.time() - start_time < timeout_seconds:
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        f"{base}/v1/chat/completions",
                        json={
                            "model": model_name,
                            "messages": [{"role": "user", "content": "hi"}],
                            "max_tokens": 1,
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
        if path not in self.get_supported_endpoints():
            return False, f"模型 '{model_name}' 的接口插件不支持路径 '{path}'"
        return True, ""
