"""30s 心跳:运行中日志会话/计费运行段的 last_active 定期落库。

崩溃/强杀(如直接关机)后,启动收口(log_close_open_model_sessions /
close_open_runtime_sessions)以 last_active(≈死亡时刻,误差 ≤ 心跳间隔)
作 end_time——时长与计费不歪到启动时刻。end_time 仍仅退出时写(NULL=运行中,
前端状态语义不变);last_active 是独立心跳列,不与状态耦合。"""
from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from llm_manager.data import logs as _logs
from llm_manager.data.usage import runtime_heartbeat_live

if TYPE_CHECKING:
    from llm_manager.data.persistence import Db

HEARTBEAT_INTERVAL = 30.0  # 秒:老项目同款节奏,崩溃最多丢最后 30s


async def heartbeat_loop(db: "Db", stop_event: asyncio.Event,
                         interval: float = HEARTBEAT_INTERVAL) -> None:
    """常驻心跳任务:每 interval 给所有进行中会话/运行段写 last_active。"""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except asyncio.TimeoutError:
            now = time.time()
            _logs.log_heartbeat_live(db, now)
            runtime_heartbeat_live(db, now)
        except asyncio.CancelledError:
            break
