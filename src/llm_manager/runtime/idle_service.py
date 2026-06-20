"""IdleCheckService — STUB Service (no-op loop)."""

from __future__ import annotations

from llm_manager.ports.runtime import ModelRuntimePort


class IdleCheckService:
    def __init__(self, runtime: ModelRuntimePort) -> None:
        self._runtime = runtime

    def start(self) -> None:
        # TODO(phase-runtime): 30s idle-timeout auto-stop loop.
        return None

    def stop(self) -> None:
        return None
