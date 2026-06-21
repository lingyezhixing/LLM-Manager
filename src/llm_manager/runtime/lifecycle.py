"""Lifecycle (start/stop pipeline). STUB — filled in Plan 3."""
from __future__ import annotations


class Lifecycle:
    async def ensure_running(self, alias: str) -> None:
        raise NotImplementedError("Plan 3")

    async def stop(self, alias: str) -> None:
        raise NotImplementedError("Plan 3")

    async def unload_all(self) -> None:
        raise NotImplementedError("Plan 3")
