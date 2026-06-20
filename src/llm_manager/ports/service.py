"""Lifecycle contract for managed background services (start/stop/join)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Service(Protocol):
    def start(self) -> None: ...

    def stop(self) -> None: ...
