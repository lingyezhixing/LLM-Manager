"""ProcessBackend port — substitutable process supervision (subprocess today,
containerized/docker later)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True, slots=True)
class ProcessHandle:
    """Opaque handle to a spawned process (pid + internal name)."""

    name: str
    pid: int


@runtime_checkable
class ProcessBackend(Protocol):
    def spawn(
        self,
        name: str,
        command: list[str],
        *,
        on_output: Callable[[str], None] | None = None,
    ) -> ProcessHandle: ...

    def kill_tree(self, handle: ProcessHandle) -> None: ...

    def is_alive(self, handle: ProcessHandle) -> bool: ...

    def on_unexpected_exit(self, callback: Callable[[str], None]) -> None: ...
