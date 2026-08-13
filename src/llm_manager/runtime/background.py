"""Background loops: idle reclamation + auto-start + 30s heartbeat + log retention."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

from llm_manager import state
from llm_manager.data import logs as _logs
from llm_manager.data.usage import runtime_heartbeat_live
from llm_manager.runtime.loops import tick_loop

if TYPE_CHECKING:
    from llm_manager.data.config_store import ConfigStore
    from llm_manager.data.persistence import Db

logger = logging.getLogger(__name__)

AUTO_START_MARGIN: float = 30.0
HEARTBEAT_INTERVAL = 30.0  # 秒:老项目同款节奏,崩溃最多丢最后 30s


def _plan_batches(models_schemes: list) -> tuple[list[str], list[str]]:
    """设备隔离贪心:scheme.required_devices 与并行批已占无交集 → parallel(累计 occupied);
    有交集 → serial。纯函数,无 IO。"""
    parallel: list[str] = []
    occupied: set[str] = set()
    serial: list[str] = []
    for name, scheme in models_schemes:
        if scheme.required_devices & occupied:
            serial.append(name)
        else:
            parallel.append(name)
            occupied |= scheme.required_devices
    return parallel, serial


def select_idle_candidates(alive_sec: float, now: float) -> list[str]:
    """只读 state:ROUTING ∩ pending==0 ∩ idle>alive_sec。now 注入(可测)。
    相对 state 全局确定,但非引用透明:读模块级 _state(区别于 scheduling 注入快照的纯函数)。"""
    return [
        n
        for n in state.routing_names()
        if state.pending_count(n) == 0 and (now - state.get_last_access(n)) > alive_sec
    ]


async def idle_reclamation_loop(
    lifecycle, get_cfg, stop_event: asyncio.Event, *, period: float = 30.0
) -> None:
    """每轮从 get_cfg() 取 fresh alive_time(P1 写回后即时生效)。alive_time<=0 禁用。
    单轮异常记日志继续(与 log_retention_loop 同款兜底)。"""

    async def _tick() -> None:
        alive_sec = get_cfg().program.alive_time * 60.0
        if alive_sec <= 0:
            logger.info("idle reclamation disabled (alive_time<=0)")
            return
        now = time.monotonic()
        for name in select_idle_candidates(alive_sec, now):
            # 二次确认(0-await 间隙防护):临界段内 logger 走同步 handler 不 yield
            if state.pending_count(name) > 0:
                logger.info("skip reclaim %s: new request in flight", name)
                continue
            logger.info("idle reclaim %s (idle %.0fs)", name, now - state.get_last_access(name))
            try:
                await lifecycle.stop(name)
            except Exception as e:  # noqa: BLE001
                logger.error("idle reclaim stop failed %s: %s", name, e)

    def _on_error(e: Exception) -> None:
        logger.error("idle reclamation iteration error: %s", e)

    await tick_loop(stop_event, period, _tick, on_error=_on_error)


async def auto_start(
    lifecycle, models: list[str], cfg, monitor, *, timeout: float, stop_event: asyncio.Event
) -> None:
    """设备隔离分批调度:扫描硬件 → select_adaptive → _plan_batches
    → parallel gather(spawn 锁串行 spawn,probe 并行)+ serial 逐一(refresh 缓存刷新)。"""
    if not models:
        logger.info("no auto_start models")
        return
    logger.info("auto_start %d models: %s", len(models), models)
    from llm_manager import config as _cfg

    async def _one(name: str) -> None:
        if stop_event.is_set():
            return
        try:
            status = await asyncio.wait_for(lifecycle.ensure_running(name), timeout)
            logger.info("auto_start %s -> %s", name, status.value)
        except TimeoutError:
            logger.warning("auto_start %s timeout (%.0fs)", name, timeout)
        except Exception as e:  # noqa: BLE001
            logger.error("auto_start %s failed: %s", name, e)

    # 1. 扫描硬件
    await asyncio.to_thread(monitor.refresh)
    online = monitor.online_devices()
    # 2. 收集需求(无 scheme 跳过)
    planned = []
    for name in models:
        scheme = _cfg.select_adaptive(cfg.models[name], online)
        if scheme is None:
            required = sorted(_cfg.required_devices(cfg.models[name]))
            logger.info(
                "auto_start skip %s: no adaptive scheme (required %s, online %s)",
                name,
                required,
                sorted(online),
            )
        else:
            planned.append((name, scheme))
    # 3. 设备隔离分批
    parallel, serial = _plan_batches(planned)
    logger.info("auto_start parallel=%s serial=%s", parallel, serial)
    # 4. 并行批(设备互斥,spawn 锁串行 spawn + probe 并行)
    if parallel:
        await asyncio.gather(*[_one(n) for n in parallel], return_exceptions=True)
    # 5. 串行队列(设备冲突,逐一 refresh 缓存刷新)
    for name in serial:
        if stop_event.is_set():
            break
        await asyncio.to_thread(monitor.refresh)
        await _one(name)
    logger.info("auto_start batch complete")


# ---------- heartbeat ----------


async def heartbeat_loop(
    db: Db, stop_event: asyncio.Event, interval: float = HEARTBEAT_INTERVAL
) -> None:
    """常驻心跳任务:每 interval 把所有进行中会话/运行段的 end_time 推到 now。
    wait_first=True:启动先睡一轮再首次写(启动即写无意义,且让 DB 连接先行就绪)。

    运行中标识由内存态表达(logs._sessions / usage._live_segments),end_time 只管时间、
    不兼任状态——故心跳可直接写 end_time 而不破坏「运行中」语义。崩溃/强杀(如直接
    关机)后 end_time 停在最后一次心跳(≈死亡时刻,误差 ≤ 心跳间隔);新进程内存集合
    为空,残留会话/段天然 status=ended,无需启动收口。模型正常停止时 lifecycle 再写
    一次精确 end_time(最终值)。"""

    async def _tick() -> None:
        now = time.time()
        # SQLite 写移出事件循环线程(与 log_cleanup 一致);两个 UPDATE 均为极小写。
        await asyncio.to_thread(_logs.log_heartbeat_live, db, now)
        await asyncio.to_thread(runtime_heartbeat_live, db, now)

    await tick_loop(stop_event, interval, _tick, wait_first=True)


# ---------- log retention ----------


def retention_from_store(store: ConfigStore) -> tuple[int, int]:
    """retention 接线:单次快照取 days/count(log_retention_loop 每轮注入)。"""
    p = store.snapshot().program
    return p.log_retention_days, p.log_retention_count


async def log_retention_loop(
    db, get_settings, stop_event: asyncio.Event, *, period: float = 60.0, now: float | None = None
) -> None:
    """每轮取 fresh 保留规则执行 log_cleanup。get_settings 注入(可测)。
    单轮异常记日志继续(与 idle_reclamation_loop 同款兜底)。

    两条常驻规则(保 N 天 / 保 N 会话)每轮独立执行,谁先触发谁先清。
    live_session_ids 传给 log_cleanup 排除(belt-and-braces:保留规则不删直播会话,
    否则其 DB 行被删后 logs.flush 落库 FK 失败)。"""

    async def _tick() -> None:
        days, count = get_settings()
        if days > 0 and count > 0:
            removed_s, removed_l = await asyncio.to_thread(
                _logs.log_cleanup,
                db,
                days,
                count,
                now,
                live_session_ids=_logs.live_session_ids(),
            )
            if removed_s:
                logger.info("log retention cleaned %d sessions / %d lines", removed_s, removed_l)

    def _on_error(e: Exception) -> None:
        logger.error("log retention iteration error: %s", e)

    await tick_loop(stop_event, period, _tick, on_error=_on_error)
