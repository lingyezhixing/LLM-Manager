"""SubprocessBackend — STUB. Real cross-platform tree-kill comes in the process phase."""

from __future__ import annotations

from collections.abc import Callable

from llm_manager.ports.process import ProcessHandle


class SubprocessBackend:
    """Stub: no process supervision yet. Satisfies ports.process.ProcessBackend."""

    def spawn(
        self, name: str, command: list[str], *, on_output: Callable[[str], None] | None = None
    ) -> ProcessHandle:
        raise NotImplementedError("process backend not implemented")  # TODO(phase-process)

    def kill_tree(self, handle: ProcessHandle) -> None:
        raise NotImplementedError("process backend not implemented")  # TODO(phase-process)

    def is_alive(self, handle: ProcessHandle) -> bool:
        raise NotImplementedError("process backend not implemented")  # TODO(phase-process)

    def on_unexpected_exit(self, callback: Callable[[str], None]) -> None:
        # TODO(phase-process): wire the reaper to this callback.
        return None
