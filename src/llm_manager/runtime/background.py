"""Background loops: idle reclamation + auto-start. Plan 5."""
from __future__ import annotations

import asyncio
import logging
import time

from llm_manager import state

logger = logging.getLogger(__name__)

AUTO_START_MARGIN: float = 30.0


def select_idle_candidates(alive_sec: float, now: float) -> list[str]:
    """只读 state:ROUTING ∩ pending==0 ∩ idle>alive_sec。now 注入(可测)。
    相对 state 全局确定,但非引用透明(读模块级 _state,区别于 scheduling 主 spec §4.4 注入快照的纯函数)。"""
    return [n for n in state.routing_names()
            if state.pending_count(n) == 0 and (now - state.get_last_access(n)) > alive_sec]


async def idle_reclamation_loop(lifecycle, alive_sec: float, stop_event: asyncio.Event, *, period: float = 30.0) -> None:
    raise NotImplementedError("Plan 5: idle loop (Task 4)")


async def auto_start(lifecycle, models: list[str], *, timeout: float, stop_event: asyncio.Event) -> None:
    raise NotImplementedError("Plan 5: auto_start (Task 5)")
