from __future__ import annotations

import logging
import os
import subprocess
import threading
import time
from dataclasses import dataclass

from llm_manager.container import Container
from llm_manager.services.base import BaseService

logger = logging.getLogger(__name__)


@dataclass
class ProcessInfo:
    pid: int
    name: str
    process: subprocess.Popen
    started_at: float


class ProcessManager(BaseService):
    def __init__(self, container: Container):
        super().__init__(container)
        self._processes: dict[str, ProcessInfo] = {}
        self._lock = threading.Lock()

    async def start_process(
        self,
        name: str,
        script_path: str,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ProcessInfo:
        if name in self._processes:
            raise ValueError(f"Process '{name}' already running")

        merged_env = {**os.environ, **(env or {})}

        process = subprocess.Popen(
            script_path if os.name == "nt" else ["/bin/bash", script_path],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            cwd=cwd,
            env=merged_env,
            shell=(os.name == "nt"),
        )

        info = ProcessInfo(
            pid=process.pid,
            name=name,
            process=process,
            started_at=time.time(),
        )

        with self._lock:
            self._processes[name] = info

        logger.info("Process '%s' started (pid=%d)", name, process.pid)
        return info

    async def stop_process(self, name: str, timeout: float = 10.0) -> None:
        with self._lock:
            info = self._processes.pop(name, None)

        if info is None:
            return

        process = info.process
        try:
            process.terminate()
            try:
                process.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        except Exception:
            logger.exception("Error stopping process '%s'", name)

        logger.info("Process '%s' stopped (pid=%d)", name, info.pid)

    def get_process(self, name: str) -> ProcessInfo | None:
        return self._processes.get(name)

    def is_running(self, name: str) -> bool:
        info = self._processes.get(name)
        if info is None:
            return False
        return info.process.poll() is None

    async def on_stop(self) -> None:
        names = list(self._processes.keys())
        for name in names:
            await self.stop_process(name)
