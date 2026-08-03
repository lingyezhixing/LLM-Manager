"""Log retention loop: enforces the two always-active rules (keep N days / keep N
sessions) over log_sessions. Both rules run independently every period — whichever
fires first cleans first. Reads fresh days/count from the injected get_settings
each round, mirrors idle_reclamation_loop (stop_event-interruptible sleep,
error-guarded).
"""
from __future__ import annotations

import asyncio
import logging

# 取模块级 live 会话传给 log_cleanup 排除(I1 belt-and-braces:保留规则不删直播会话,
# 否则其 DB 行被删后 logs.flush 落库 FK 失败)。导入安全无环:logs 仅依赖
# persistence / realtime(→devices),均不依赖 runtime.log_retention。
from llm_manager.data import logs as _logs
from llm_manager.data import persistence as _p

logger = logging.getLogger(__name__)


async def log_retention_loop(db, get_settings, stop_event: asyncio.Event,
                             *, period: float = 60.0, now: float | None = None) -> None:
    """每轮取 fresh 保留规则执行 log_cleanup。get_settings 注入(可测)。"""
    while not stop_event.is_set():
        try:
            days, count = get_settings()
            if days > 0 and count > 0:
                removed_s, removed_l = await asyncio.to_thread(
                    _p.log_cleanup, db, days, count, now,
                    live_session_ids=set(_logs._sessions))
                if removed_s:
                    logger.info("log retention cleaned %d sessions / %d lines",
                                removed_s, removed_l)
        except Exception as e:
            logger.error("log retention iteration error: %s", e)
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=period)
        except asyncio.TimeoutError:
            pass
