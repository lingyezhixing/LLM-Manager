"""30s 心跳:把运行中日志会话/计费运行段的 end_time 定期推到 now。

运行中标识由内存态表达(logs._sessions / usage._live_segments),end_time 只管时间、
不兼任状态——故心跳可直接写 end_time 而不破坏「运行中」语义。崩溃/强杀(如直接
关机)后 end_time 停在最后一次心跳(≈死亡时刻,误差 ≤ 心跳间隔);新进程内存集合
为空,残留会话/段天然 status=ended,无需启动收口。模型正常停止时 lifecycle 再写
一次精确 end_time(最终值)。"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from llm_manager.data import logs as _logs
from llm_manager.data.usage import runtime_heartbeat_live

if TYPE_CHECKING:
    from llm_manager.data.persistence import Db

HEARTBEAT_INTERVAL = 30.0  # 秒:老项目同款节奏,崩溃最多丢最后 30s


async def heartbeat_loop(
    db: Db, stop_event: asyncio.Event, interval: float = HEARTBEAT_INTERVAL
) -> None:
    """常驻心跳任务:每 interval 把所有进行中会话/运行段的 end_time 推到 now。"""
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except TimeoutError:
            now = time.time()
            _logs.log_heartbeat_live(db, now)
            runtime_heartbeat_live(db, now)
        except asyncio.CancelledError:
            break
