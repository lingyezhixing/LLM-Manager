"""In-process system operations (extracted from tray.py). Called directly by
hosts (e.g. the future desktop tray), NOT over HTTP."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from llm_manager.domain.result import OperationResult


@runtime_checkable
class SystemOps(Protocol):
    def wake_on_lan(self) -> OperationResult: ...

    def switch_claude_config(self, preset: str) -> OperationResult: ...

    def restart_autostart(self) -> OperationResult: ...

    def unload_all(self) -> OperationResult: ...
