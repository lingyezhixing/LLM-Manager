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
import os
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from llm_manager import state
from llm_manager.config import AppConfig, ModelConfig, Scheme, resolve_alias, select_adaptive, substitute_vars
from llm_manager.data import logs as _logs
from llm_manager.probes import ProbeResult
from llm_manager.runtime import scheduling
from llm_manager.state import ModelStatus

if TYPE_CHECKING:
    from llm_manager.data.persistence import Db

logger = logging.getLogger(__name__)


class Lifecycle:
    def __init__(
        self,
        *,
        get_cfg: Callable[[], AppConfig],
        supervisor,
        devices,
        probes: dict[str, Callable],
        scheme_select=select_adaptive,
        startup_timeout: float = 60.0,
        db: "Db | None" = None,
    ) -> None:
        self._get_cfg = get_cfg
        self._supervisor = supervisor
        self._devices = devices
        self._probes = probes
        self._scheme_select = scheme_select
        self.startup_timeout = startup_timeout
        self._db = db
        self._stop_events: dict[str, asyncio.Event] = {}
        self._active_schemes: dict[str, Scheme] = {}
        self._spawn_lock = asyncio.Lock()   # 全局 spawn 锁:并发 spawn 串行,防显存超量
        self._log_session_ids: dict[str, int] = {}   # alias → 进行中模型日志会话 id(多模型并发,按 alias 独立追踪)
        self._runtime_seg_ids: dict[str, int] = {}   # alias → 进行中计费运行段 id(record_runtime_end 按关,不再靠 end_time IS NULL 定位)

    # ---------- public ----------
    async def ensure_running(self, alias: str, *, inc_pending: bool = False) -> ModelStatus:
        self._reconcile(alias)
        if state.is_runnable(alias):
            status = state.get_status(alias)
            if inc_pending and status == ModelStatus.ROUTING:
                state.begin_request(alias)   # 同一无 await 临界段内 inc → 关闭 idle 回收 TOCTOU 间隙
            logger.debug("%s already %s (skip)", alias, status.value)
            return status
        future, won = state.claim_start(alias)
        if not won:
            try:
                await future
            except Exception:
                pass
            status = state.get_status(alias)
            if inc_pending and status == ModelStatus.ROUTING:
                state.begin_request(alias)
            return status
        self._stop_events[alias] = asyncio.Event()
        try:
            status = await self._run_pipeline(alias)
            state.finish_start(alias, status, owner=future)
        except asyncio.CancelledError:
            state.record_failure(alias, "startup cancelled")
            state.finish_start(alias, ModelStatus.FAILED, owner=future)
            raise
        except Exception as e:
            state.record_failure(alias, f"pipeline error: {e}")
            state.finish_start(alias, ModelStatus.FAILED, owner=future)
        status = state.get_status(alias)
        if inc_pending and status == ModelStatus.ROUTING:
            state.begin_request(alias)
        return status

    async def stop(self, alias: str) -> ModelStatus:
        if state.get_status(alias) in (ModelStatus.STOPPED, ModelStatus.FAILED):
            return state.get_status(alias)
        state.set_status(alias, ModelStatus.STOPPED, force=True, reason="user stop")
        self._runtime_end(alias)   # 关 runtime 段:必须在首个 await 前 pop alias→seg_id(防并发 restart 抢先开新段覆盖映射;按 id 关,幂等)
        self._stop_events.setdefault(alias, asyncio.Event()).set()
        pid = state.get_pid(alias)
        if pid is not None:
            await self._supervisor.kill_tree(pid)
        state.clear_pid(alias)
        self._active_schemes.pop(alias, None)
        fut = state.pop_inflight(alias)
        if fut is not None and not fut.done():
            fut.set_result(ModelStatus.STOPPED)
        self._log_end(alias)   # 收口模型日志会话(落库 end_time):下次 start 起新会话
        return state.get_status(alias)

    async def unload_all(self) -> list[str]:
        cfg = self._get_cfg()
        names = [
            n for n in cfg.models
            if state.get_status(n) not in (ModelStatus.STOPPED, ModelStatus.FAILED)
        ]
        results = await asyncio.gather(*[self.stop(n) for n in names], return_exceptions=True)
        return [n for n, r in zip(names, results) if not isinstance(r, Exception)]

    # ---------- runtime recording helpers ----------
    def _runtime_start(self, alias: str) -> None:
        if self._db is None:
            return
        try:
            from llm_manager.data import usage as _u
            seg_id = _u.record_runtime_start(self._db, alias, time.time())
            self._runtime_seg_ids[alias] = seg_id
        except Exception:
            logger.warning("record_runtime_start failed for %s", alias, exc_info=True)

    def _runtime_end(self, alias: str) -> None:
        if self._db is None:
            return
        seg_id = self._runtime_seg_ids.pop(alias, None)
        if seg_id is None:
            return   # 未开过段(exit cb 兜底重复触发)→ 幂等 no-op
        try:
            from llm_manager.data import usage as _u
            _u.record_runtime_end(self._db, seg_id, time.time())
        except Exception:
            logger.warning("record_runtime_end failed for %s", alias, exc_info=True)

    # ---------- log session recording helpers ----------
    def _log_end(self, alias: str) -> None:
        """收口模型日志会话(若开着):stop / 崩溃 / 新 spawn 前调用。"""
        sid = self._log_session_ids.pop(alias, None)
        if sid is not None:
            _logs.end_session(sid)

    # ---------- pipeline ----------
    async def _run_pipeline(self, alias: str) -> ModelStatus:
        ev = self._stop_events[alias]
        model = self._cfg_model(alias)

        await asyncio.to_thread(self._devices.refresh)
        if ev.is_set():
            return ModelStatus.STOPPED

        online = self._devices.online_devices()
        scheme = self._scheme_select(model, online)
        if scheme is None:
            # 消息带 required vs online 对比:可区分「设备真离线」与「required 名不匹配」
            # (匹配=token 全子集,如 'rtx4060' 拆不成 {rtx,4060} 永远不匹配)
            required = sorted({d for s in model.schemes.values() for d in s.required_devices})
            msg = f"no adaptive scheme (required {required}, online {sorted(online)})"
            logger.warning("%s: %s", alias, msg)
            state.record_failure(alias, msg)
            return ModelStatus.FAILED
        logger.info("cold start %s scheme=%s devices=%s",
                    alias, scheme.config_source, sorted(scheme.required_devices))

        # === spawn 锁:check_and_free + spawn 串行,避免并发 spawn 显存超量 ===
        async with self._spawn_lock:
            snap = self._devices.snapshot()
            runnable = self._runnable(exclude=alias)
            to_stop = scheduling.check_and_free(scheme.memory_mb, snap, runnable, time.monotonic())
            if to_stop:
                logger.info("evict %s to free mem for %s", list(to_stop), alias)
                await asyncio.gather(*[self.stop(n) for n in to_stop], return_exceptions=True)
                await asyncio.to_thread(self._devices.refresh)
                snap = self._devices.snapshot()   # re-snapshot after eviction
            if not self._deficit_satisfied(scheme.memory_mb, snap):
                logger.warning("%s: insufficient resource after eviction", alias)
                state.record_failure(alias, "insufficient resource after eviction")
                return ModelStatus.FAILED
            if ev.is_set():
                return ModelStatus.STOPPED

            c = scheme.command
            # 变量替换({{port}}/{{alias}}):顶部端口/别名修改自动传导到启动命令;无占位符原样。
            exe = substitute_vars(c.exe, model)
            args = [substitute_vars(a, model) for a in c.args]
            if c.conda_env:
                conda_prefix = ["conda", "run", "-n", c.conda_env, "--no-capture-output"]
                argv = (["cmd", "/c", *conda_prefix, exe, *args] if os.name == "nt"
                        else [*conda_prefix, exe, *args])
            else:
                argv = [exe, *args]
            env = {**os.environ, **c.env}
            rec = await self._supervisor.spawn(
                argv, env=env, cwd=c.cwd,
                on_output=lambda line, stream: _logs.capture(alias, line, stream))
            logger.info("spawn %s pid=%d", alias, rec.pid)

            # === 模型日志会话:先收口上一会话(防快速 restart 残留),再开新会话。
            # 失败仅降级(该模型本次日志不落库),不阻断 spawn:spawn 锁内不得抛。===
            try:
                self._log_end(alias)
                self._log_session_ids[alias] = _logs.start_session(
                    "model", model_name=alias, alias=model.aliases[0])
            except Exception:
                logger.warning("log session start failed for %s", alias, exc_info=True)

            # === post-spawn 无-await 临界段 ===
            state.record_pid(alias, rec.pid)
            orphan_pid = rec.pid if ev.is_set() else None
            # === end critical section ===
        # === 锁外:orphan kill + probe 并行 ===
        if orphan_pid is not None:
            await self._supervisor.kill_tree(orphan_pid)
            self._log_end(alias)   # stop 在 spawn await 中到达(会话于其 _log_end 之后才开)→ 必须在此收口,防泄漏 running 会话
            return ModelStatus.STOPPED

        # Any raise below must kill the spawned pid before propagating;
        # ensure_running's outer except has no rec.pid reference.
        try:
            if ev.is_set():
                return await self._abort_spawned(rec.pid)
            state.set_status(alias, ModelStatus.INIT_SCRIPT)
            state.set_status(alias, ModelStatus.HEALTH_CHECK)
            self._active_schemes[alias] = scheme

            if ev.is_set():
                return await self._abort_spawned(rec.pid)
            probe = await asyncio.to_thread(self._probe, alias, model.mode)
            logger.info("probe %s %s", alias, "ok" if probe.ok else "fail: " + str(probe.message))
            if ev.is_set():
                return await self._abort_spawned(rec.pid)
            if not probe.ok:
                await self._supervisor.kill_tree(rec.pid)
                if ev.is_set():
                    return ModelStatus.STOPPED        # kill_tree awaited; stop may have come — don't overwrite STOPPED
                self._log_end(alias)   # probe 失败不会走 on_exit / stop(FAILED 早退)→ 必须在此收口,防泄漏 running 会话
                state.record_failure(alias, f"probe failed: {probe.message}")
                return ModelStatus.FAILED

            # === set-ROUTING 无-await 临界段 ===
            if ev.is_set():
                return await self._abort_spawned(rec.pid)
            state.set_status(alias, ModelStatus.ROUTING)
            state.touch_activity(alias)
            self._runtime_start(alias)
            self._supervisor.on_exit(rec.pid, lambda code: self._on_crash(alias, code))
            logger.info("%s -> routing", alias)
            return ModelStatus.ROUTING
        except (Exception, asyncio.CancelledError):
            await self._supervisor.kill_tree(rec.pid)
            self._log_end(alias)   # 异常/取消路径同样收口(会话已在 spawn 打开)
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
            self._runtime_end(alias)
            self._log_end(alias)   # 进程崩溃 → 收口日志会话
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
            self._runtime_end(alias)   # exit cb 漏触发时兜底关会话(无开会话则 0 行 UPDATE,幂等)
            self._log_end(alias)       # 与 _on_crash 对称:exit cb 漏触发时也收口日志会话,防滞留直播集
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
        # 委托 config.resolve_alias,避免与它重复实现别名解析循环
        cfg = self._get_cfg()
        return cfg.models[resolve_alias(cfg, alias)]

    def _runnable(self, exclude: str) -> dict[str, scheduling.RunnableInfo]:
        cfg = self._get_cfg()
        out: dict[str, scheduling.RunnableInfo] = {}
        for name in cfg.models:
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
        model = self._cfg_model(alias)
        served = model.aliases[0]  # aliases[0]=主别名=下游 served name(lmdeploy --model-name / llama.cpp -a)
        fn = self._probes[mode]
        return fn(served, model.port, None, self.startup_timeout)
