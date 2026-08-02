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
    """(days, count) 直读 system_settings(默认 30/10;非法值回退默认)。

    防御手改 DB:值非整数(如 "abc")时若让 int() 抛 ValueError,循环每轮都会被
    异常兜底记 error 且永远不清理——回退默认值保持规则可用。"""
    def _int(key: str, default: int) -> int:
        raw = get_setting(db, key)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError:
            logger.warning("invalid log retention %s=%r, falling back to %d", key, raw, default)
            return default

    return _int("log_retention_days", 30), _int("log_retention_count", 10)


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
