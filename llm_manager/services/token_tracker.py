from __future__ import annotations

import asyncio
import json
import logging
import re
import time

from llm_manager.config.models import AppConfig
from llm_manager.container import Container
from llm_manager.database.repos.request_repo import RequestRepository
from llm_manager.schemas.request import TokenUsage
from llm_manager.services.base import BaseService

logger = logging.getLogger(__name__)


class TokenTracker(BaseService):
    """Token 提取与异步记录"""

    def __init__(self, container: Container):
        super().__init__(container)
        self._config: AppConfig | None = None
        self._request_repo: RequestRepository | None = None

    async def on_start(self) -> None:
        self._config = self._container.resolve(AppConfig)
        self._request_repo = self._container.resolve(RequestRepository)

    def extract_tokens(self, data: dict) -> TokenUsage:
        """从解析后的 JSON dict 中提取 token，优先 timings，降级 usage"""
        if "timings" in data:
            timings = data["timings"]
            cache_n = timings.get("cache_n", 0)
            prompt_n = timings.get("prompt_n", 0)
            predicted_n = timings.get("predicted_n", 0)
            input_tokens = cache_n + prompt_n
            output_tokens = predicted_n
            if any([input_tokens, output_tokens, cache_n, prompt_n]):
                return TokenUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_n=cache_n,
                    prompt_n=prompt_n,
                )

        if "usage" in data:
            usage = data["usage"]
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)
            if any([input_tokens, output_tokens]):
                return TokenUsage(
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    cache_n=0,
                    prompt_n=0,
                )

        return TokenUsage()

    def extract_from_stream(self, content: bytes) -> TokenUsage:
        """从流式响应（SSE 或 JSON）中倒序提取 token"""
        try:
            content_str = content.decode("utf-8")
        except UnicodeDecodeError:
            return TokenUsage()

        def _reversed_blocks():
            if "data: " in content_str:
                for line in reversed(content_str.splitlines()):
                    if line.startswith("data: "):
                        data_str = line[6:].strip()
                        if data_str and data_str != "[DONE]":
                            yield data_str
            else:
                try:
                    json.loads(content_str)
                    yield content_str
                except json.JSONDecodeError:
                    json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
                    for match in reversed(re.findall(json_pattern, content_str)):
                        yield match

        block_gen = _reversed_blocks()
        first_10: list[str] = []
        for _ in range(10):
            try:
                first_10.append(next(block_gen))
            except StopIteration:
                break

        if not first_10:
            return TokenUsage()

        for block in first_10:
            try:
                result = self.extract_tokens(json.loads(block))
                if result.input_tokens or result.output_tokens:
                    return result
            except (json.JSONDecodeError, TypeError):
                continue

        for block in block_gen:
            try:
                result = self.extract_tokens(json.loads(block))
                if result.input_tokens or result.output_tokens:
                    return result
            except (json.JSONDecodeError, TypeError):
                continue

        return TokenUsage()

    async def record_request(
        self,
        model_name: str,
        usage: TokenUsage,
        start_time: float,
        end_time: float,
        mode: str,
    ) -> None:
        """异步记录 token 到数据库"""
        if not self._config.program.should_track_tokens(mode):
            return
        if not any([usage.input_tokens, usage.output_tokens, usage.cache_n, usage.prompt_n]):
            return

        final_end = end_time if end_time > 0 else time.time()
        final_start = start_time if start_time > 0 else final_end

        try:
            await asyncio.to_thread(
                self._request_repo.save_request,
                model_name,
                final_start,
                final_end,
                usage.input_tokens,
                usage.output_tokens,
                usage.cache_n,
                usage.prompt_n,
            )
            logger.debug(
                "Token recorded: model=%s input=%d output=%d",
                model_name, usage.input_tokens, usage.output_tokens,
            )
        except Exception:
            logger.exception("Failed to record tokens for model '%s'", model_name)

    async def wrap_streaming_response(
        self,
        model_name: str,
        response,
        start_time: float,
        mode: str,
    ):
        """流式响应包装器：转发数据 + 结束后提取并记录 token"""
        chunks: list[bytes] = []
        try:
            async for chunk in response.aiter_bytes():
                chunks.append(chunk)
                yield chunk
        finally:
            end_time = time.time()
            await response.aclose()
            try:
                full_content = b"".join(chunks)
                usage = self.extract_from_stream(full_content)
                if any([usage.input_tokens, usage.output_tokens, usage.cache_n, usage.prompt_n]):
                    await self.record_request(model_name, usage, start_time, end_time, mode)
            except Exception:
                logger.exception(
                    "Stream token extraction failed for model '%s'", model_name,
                )
