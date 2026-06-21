"""Model lifecycle: start/stop coroutine pipeline + asyncio.Event cooperative
interruption + single-dispatch + crash->FAILED + reconcile safety net.

Single-threaded event loop -> state access needs no locks; "check stop-signal +
mutate state" sequences are await-free critical sections (atomic by cooperation).
ensure_running ALWAYS returns the real status (stop's force can overwrite).
finish_start carries an owner-token: a stale winner whose slot was popped by
stop (and possibly re-claimed by a concurrent restart) becomes a no-op instead
of clobbering the new owner."""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from llm_manager import state
from llm_manager.config import AppConfig, ModelConfig, Scheme, select_adaptive
from llm_manager.probes import ProbeResult
from llm_manager.runtime import scheduling
from llm_manager.state import ModelStatus

logger = logging.getLogger(__name__)


class Lifecycle:
    def __init__(
        self,
        *,
        cfg: AppConfig,
        supervisor,
        devices,
        probes: dict[str, Callable],
        scheme_select=select_adaptive,
        startup_timeout: float = 60.0,
    ) -> None:
        self._cfg = cfg
        self._supervisor = supervisor
        self._devices = devices
        self._probes = probes
        self._scheme_select = scheme_select
        self._startup_timeout = startup_timeout
        self._stop_events: dict[str, asyncio.Event] = {}
        self._active_schemes: dict[str, Scheme] = {}

    # ---------- public ----------
    async def ensure_running(self, alias: str) -> ModelStatus:
        self._reconcile(alias)
        if state.is_runnable(alias):
            return state.get_status(alias)
        future, won = state.claim_start(alias)
        if not won:
            try:
                await future
            except Exception:
                pass
            return state.get_status(alias)
        self._stop_events[alias] = asyncio.Event()
        try:
            status = await self._run_pipeline(alias)
            state.finish_start(alias, status, owner=future)
        except Exception as e:
            state.record_failure(alias, f"pipeline error: {e}")
            state.finish_start(alias, ModelStatus.FAILED, owner=future)
        return state.get_status(alias)

    async def stop(self, alias: str) -> ModelStatus:
        if state.get_status(alias) in (ModelStatus.STOPPED, ModelStatus.FAILED):
            return state.get_status(alias)
        state.set_status(alias, ModelStatus.STOPPED, force=True, reason="user stop")
        self._stop_events.setdefault(alias, asyncio.Event()).set()
        pid = state.get_pid(alias)
        if pid is not None:
            await self._supervisor.kill_tree(pid)
        state.clear_pid(alias)
        self._active_schemes.pop(alias, None)
        fut = state.pop_inflight(alias)
        if fut is not None and not fut.done():
            fut.set_result(ModelStatus.STOPPED)
        return state.get_status(alias)

    async def unload_all(self) -> list[str]:
        names = [
            n for n in self._cfg.models
            if state.get_status(n) not in (ModelStatus.STOPPED, ModelStatus.FAILED)
        ]
        results = await asyncio.gather(*[self.stop(n) for n in names], return_exceptions=True)
        return [n for n, r in zip(names, results) if not isinstance(r, Exception)]

    # ---------- pipeline ----------
    async def _run_pipeline(self, alias: str) -> ModelStatus:
        ev = self._stop_events[alias]
        model = self._cfg_model(alias)

        await asyncio.to_thread(self._devices.refresh)
        if ev.is_set():
            return ModelStatus.STOPPED

        scheme = self._scheme_select(model, self._devices.online_devices())
        if scheme is None:
            state.record_failure(alias, "no adaptive scheme (devices offline)")
            return ModelStatus.FAILED

        snap = self._devices.snapshot()
        runnable = self._runnable(exclude=alias)
        to_stop = scheduling.check_and_free(scheme.memory_mb, snap, runnable, time.monotonic())
        if to_stop:
            await asyncio.gather(*[self.stop(n) for n in to_stop], return_exceptions=True)
            snap = self._devices.snapshot()   # re-snapshot after eviction (spec §8)

        if not self._deficit_satisfied(scheme.memory_mb, snap):
            state.record_failure(alias, "insufficient resource after eviction")
            return ModelStatus.FAILED
        if ev.is_set():
            return ModelStatus.STOPPED

        cmd = [str(scheme.script_path)]
        rec = await self._supervisor.spawn(cmd)

        # === post-spawn critical section (no await) === invariant 3
        state.record_pid(alias, rec.pid)
        orphan_pid = rec.pid if ev.is_set() else None
        # === end critical section ===
        if orphan_pid is not None:
            await self._supervisor.kill_tree(orphan_pid)
            return ModelStatus.STOPPED

        # Any raise below must kill the spawned pid before propagating;
        # ensure_running's outer except has no rec.pid reference (guard D).
        try:
            if ev.is_set():
                return await self._abort_spawned(rec.pid)
            state.set_status(alias, ModelStatus.INIT_SCRIPT)
            state.set_status(alias, ModelStatus.HEALTH_CHECK)
            self._active_schemes[alias] = scheme

            if ev.is_set():
                return await self._abort_spawned(rec.pid)
            probe = await asyncio.to_thread(self._probe, alias, model.mode)
            if ev.is_set():
                return await self._abort_spawned(rec.pid)
            if not probe.ok:
                await self._supervisor.kill_tree(rec.pid)
                if ev.is_set():
                    return ModelStatus.STOPPED        # kill_tree awaited; stop may have come — don't overwrite STOPPED (guard H)
                state.record_failure(alias, f"probe failed: {probe.message}")
                return ModelStatus.FAILED

            # === set-ROUTING critical section (no await) === invariant 2
            if ev.is_set():
                return await self._abort_spawned(rec.pid)
            state.set_status(alias, ModelStatus.ROUTING)
            state.touch_activity(alias)
            self._supervisor.on_exit(rec.pid, lambda code: self._on_crash(alias, code))
            return ModelStatus.ROUTING
        except Exception:
            await self._supervisor.kill_tree(rec.pid)
            raise

    async def _abort_spawned(self, pid: int | None) -> ModelStatus:
        if pid is not None:
            await self._supervisor.kill_tree(pid)
        return ModelStatus.STOPPED

    # ---------- crash / reconcile ----------
    def _on_crash(self, alias: str, code: int) -> None:
        try:
            if state.get_status(alias) == ModelStatus.STOPPED:
                return
            state.record_failure(alias, f"process exited code={code}")
        except Exception as e:
            logger.error("on_exit callback error for %s: %s", alias, e)

    def _reconcile(self, alias: str) -> None:
        s = state.get_status(alias)
        if s in (ModelStatus.STOPPED, ModelStatus.FAILED):
            return
        pid = state.get_pid(alias)
        alive = pid is not None and self._supervisor.alive(pid)
        if s == ModelStatus.ROUTING and not alive:
            state.record_failure(alias, f"reconcile: process dead (pid={pid})")
        elif s in (ModelStatus.STARTING, ModelStatus.INIT_SCRIPT, ModelStatus.HEALTH_CHECK) \
                and not state.has_inflight(alias):
            state.record_failure(alias, f"reconcile: orphan {s.name} (no inflight)")
            state.clear_pid(alias)
            state.clear_inflight(alias)

    # ---------- helpers ----------
    def _deficit_satisfied(self, required: dict[str, int], snap: dict) -> bool:
        avail = {dev: info.available_memory_mb for dev, info in snap.items()}
        return not scheduling.compute_deficit(required, avail)

    def _cfg_model(self, alias: str) -> ModelConfig:
        for m in self._cfg.models.values():
            if alias == m.primary_name or alias in m.aliases:
                return m
        raise KeyError(alias)

    def _runnable(self, exclude: str) -> dict[str, scheduling.RunnableInfo]:
        out: dict[str, scheduling.RunnableInfo] = {}
        for name in self._cfg.models:
            if name == exclude or not state.is_runnable(name):
                continue
            scheme = self._active_schemes.get(name)
            out[name] = scheduling.RunnableInfo(
                mem_mb=dict(scheme.memory_mb) if scheme else {},
                pending=state.pending_count(name),
                last_access=state.get_last_access(name),
            )
        return out

    def _probe(self, alias: str, mode: str) -> ProbeResult:
        port = self._cfg_model(alias).port
        fn = self._probes[mode]
        return fn(alias, port, None, self._startup_timeout)
