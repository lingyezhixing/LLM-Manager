"""ReaperService — STUB Service (no-op loop)."""

from __future__ import annotations

from llm_manager.ports.process import ProcessBackend


class ReaperService:
    def __init__(self, backend: ProcessBackend) -> None:
        self._backend = backend

    def start(self) -> None:
        # TODO(phase-process): 5s liveness poll -> backend.on_unexpected_exit.
        return None

    def stop(self) -> None:
        return None
