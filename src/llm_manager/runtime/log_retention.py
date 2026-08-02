"""Log retention loop: enforces the two always-active rules (keep N days / keep N
sessions) over log_sessions. Both rules run independently every period — whichever
fires first cleans first. Reads fresh days/count from system_settings each round,
mirrors idle_reclamation_loop (stop_event-interruptible sleep, error-guarded).
"""
from __future__ import annotations

import asyncio
import logging

from llm_manager.data import persistence as _p
from llm_manager.data.config_store import get_setting

logger = logging.getLogger(__name__)


def retention_settings(db) -> tuple[int, int]:
    """(days, count) 直读 system_settings(默认 30/10)。"""
    days = int(get_setting(db, "log_retention_days") or 30)
    count = int(get_setting(db, "log_retention_count") or 10)
    return days, count


async def log_retention_loop(db, get_settings, stop_event: asyncio.Event,
                             *, period: float = 60.0, now: float | None = None) -> None:
    """每轮取 fresh 保留规则执行 log_cleanup。get_settings 注入(可测)。"""
    while not stop_event.is_set():
        try:
            days, count = get_settings()
            if days > 0 and count > 0:
                removed_s, removed_l = await asyncio.to_thread(
                    _p.log_cleanup, db, days, count, now)
                if removed_s:
                    logger.info("log retention cleaned %d sessions / %d lines",
                                removed_s, removed_l)
        except Exception as e:
            logger.error("log retention iteration error: %s", e)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=period)
        except asyncio.TimeoutError:
            pass
