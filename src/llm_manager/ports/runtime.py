"""Model lifecycle port — what the gateway calls to ensure a model is running."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from llm_manager.domain.result import EnsureResult, StartResult, StopResult
from llm_manager.domain.status import ModelStatus


@runtime_checkable
class ModelRuntimePort(Protocol):
    def start(self, primary: str) -> StartResult: ...

    def stop(self, primary: str) -> StopResult: ...

    def status(self, primary: str) -> ModelStatus: ...

    async def ensure_running(self, primary: str) -> EnsureResult: ...

    def begin_request(self, primary: str) -> None: ...

    def end_request(self, primary: str) -> None: ...
