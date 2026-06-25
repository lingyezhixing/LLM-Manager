"""Cross-platform process supervisor. Process-group/session isolation is an
INTERNAL invariant (Win CREATE_NEW_PROCESS_GROUP, POSIX start_new_session).
One asyncio wait-task per process replaces the legacy 5s poller. Blocking ops
(Popen, psutil.wait, killpg) run via asyncio.to_thread."""
from __future__ import annotations

import asyncio
import os
import signal
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

import psutil


@dataclass(frozen=True, slots=True)
class ProcessRecord:
    pid: int
    started_at: float
    exit_code: int | None = None


@runtime_checkable
class ProcessRunner(Protocol):
    async def spawn(
        self,
        cmd,
        *,
        shell: bool = True,
        on_output: Callable[[str, str], None] | None = None,
    ) -> ProcessRecord: ...
    async def terminate(self, pid: int, timeout: float = 10.0) -> bool: ...
    async def kill_tree(self, pid: int) -> bool: ...
    def alive(self, pid: int) -> bool: ...
    def on_exit(self, pid: int, cb: Callable[[int], None]) -> None: ...


def _popen_kwargs() -> dict:
    kw: dict = {"text": True, "encoding": "utf-8", "errors": "replace"}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kw["start_new_session"] = True
        kw["close_fds"] = True
    return kw


class Supervisor:
    def __init__(self) -> None:
        self._procs: dict[int, subprocess.Popen] = {}
        self._wait_tasks: dict[int, asyncio.Task] = {}
        self._exit_cbs: dict[int, Callable[[int], None]] = {}

    async def spawn(
        self,
        cmd,
        *,
        shell: bool = True,
        on_output: Callable[[str, str], None] | None = None,
    ) -> ProcessRecord:
        popen = await asyncio.to_thread(subprocess.Popen, cmd, shell=shell, **_popen_kwargs())
        self._procs[popen.pid] = popen
        self._wait_tasks[popen.pid] = asyncio.create_task(self._wait(popen.pid))
        return ProcessRecord(pid=popen.pid, started_at=time.monotonic())

    async def _wait(self, pid: int) -> None:
        popen = self._procs.get(pid)
        if popen is None:
            return
        rc = await asyncio.to_thread(popen.wait)
        cb = self._exit_cbs.get(pid)
        if cb:
            try:
                cb(rc if rc is not None else -1)
            except Exception:
                pass
        self._wait_tasks.pop(pid, None)   # 自清:进程已退出,释放 task 表项(防 start/stop 循环累积)

    def on_exit(self, pid: int, cb: Callable[[int], None]) -> None:
        self._exit_cbs[pid] = cb

    def alive(self, pid: int) -> bool:
        try:
            p = psutil.Process(pid)
            return p.status() != psutil.STATUS_ZOMBIE and p.is_running()
        except psutil.NoSuchProcess:
            return False
        except Exception:
            return False

    async def terminate(self, pid: int, timeout: float = 10.0) -> bool:
        try:
            p = psutil.Process(pid)
            await asyncio.to_thread(p.terminate)
            try:
                await asyncio.to_thread(p.wait, min(timeout, 5.0))
                return True
            except psutil.TimeoutExpired:
                return await self.kill_tree(pid)
        except psutil.NoSuchProcess:
            return True

    async def kill_tree(self, pid: int) -> bool:
        try:
            try:
                parent = psutil.Process(pid)
                children = parent.children(recursive=True)
                for c in children:
                    try:
                        c.kill()
                    except psutil.NoSuchProcess:
                        pass
                parent.kill()
                _, alive = psutil.wait_procs([parent] + children, timeout=3)
                if not alive:
                    return True
            except psutil.NoSuchProcess:
                return True
            except Exception:
                pass
            if os.name == "nt":
                try:
                    r = await asyncio.to_thread(
                        subprocess.run,
                        ["taskkill", "/F", "/T", "/PID", str(pid)],
                        capture_output=True,
                        timeout=5,
                    )
                    return r.returncode in (0, 128)
                except Exception:
                    return False
            else:
                try:
                    os.killpg(pid, signal.SIGKILL)
                    return True
                except ProcessLookupError:
                    return True
                except Exception:
                    return False
        finally:
            # 清理进程表(_wait 自清 _wait_tasks):Popen 句柄/cb 条目不随 start/stop 循环累积(#5)
            self._procs.pop(pid, None)
            self._exit_cbs.pop(pid, None)

    async def cleanup(self) -> None:
        for pid in list(self._procs):
            await self.kill_tree(pid)
