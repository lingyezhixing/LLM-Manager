"""Background loops: idle reclamation + auto-start. Plan 5."""
from __future__ import annotations

import asyncio
import logging
import time

from llm_manager import state

logger = logging.getLogger(__name__)

AUTO_START_MARGIN: float = 30.0


def _plan_batches(models_schemes: list) -> tuple[list[str], list[str]]:
    """设备隔离贪心:scheme.required_devices 与并行批已占无交集 → parallel(累计 occupied);
    有交集 → serial。纯函数(spec §3.1)。"""
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
    相对 state 全局确定,但非引用透明(读模块级 _state,区别于 scheduling 主 spec §4.4 注入快照的纯函数)。"""
    return [n for n in state.routing_names()
            if state.pending_count(n) == 0 and (now - state.get_last_access(n)) > alive_sec]


async def idle_reclamation_loop(lifecycle, alive_sec: float, stop_event: asyncio.Event, *, period: float = 30.0) -> None:
    if alive_sec <= 0:
        logger.info("idle reclamation disabled (alive_time<=0)")
        return
    while not stop_event.is_set():
        try:
            now = time.monotonic()
            for name in select_idle_candidates(alive_sec, now):
                # 二次确认(0-await 间隙防护):临界段内 logger 走同步 handler 不 yield
                if state.pending_count(name) > 0:
                    logger.info("skip reclaim %s: new request in flight", name)
                    continue
                logger.info("idle reclaim %s (idle %.0fs)", name, now - state.get_last_access(name))
                try:
                    await lifecycle.stop(name)
                except Exception as e:
                    logger.error("idle reclaim stop failed %s: %s", name, e)
        except Exception as e:
            logger.error("idle reclamation iteration error: %s", e)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=period)   # 可中断 sleep
        except asyncio.TimeoutError:
            pass


async def auto_start(lifecycle, models: list[str], *, timeout: float, stop_event: asyncio.Event) -> None:
    if not models:
        logger.info("no auto_start models")
        return
    logger.info("auto_start %d models: %s", len(models), models)

    async def _one(name: str) -> None:
        if stop_event.is_set():
            return
        try:
            status = await asyncio.wait_for(lifecycle.ensure_running(name), timeout)
            logger.info("auto_start %s -> %s", name, status.value)
        except asyncio.TimeoutError:
            logger.warning("auto_start %s timeout (%.0fs)", name, timeout)
        except Exception as e:
            logger.error("auto_start %s failed: %s", name, e)

    await asyncio.gather(*[_one(n) for n in models])
    logger.info("auto_start batch complete")
