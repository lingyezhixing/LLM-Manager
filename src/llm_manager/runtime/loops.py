"""常驻后台循环骨架:tick_loop 收敛 heartbeat / idle reclamation / log retention 三个
循环的「可中断 sleep + 单轮异常兜底」样板。循环语义统一:
while not stop_event.is_set(): on_tick(); 可中断 sleep(period)。
wait_first 区分「先睡一轮再首次 tick」(心跳语义)与「立即首轮」(清扫语义)。
on_error 兜底单轮异常(不传则向上抛、循环终止)。CancelledError 优雅退出。
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable


async def _interruptible_sleep(stop_event: asyncio.Event, period: float) -> None:
    """sleep period,stop_event 置位即提前醒。TimeoutError 是正常节奏,吞掉;取消抛向上。"""
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=period)
    except TimeoutError:
        pass


async def tick_loop(
    stop_event: asyncio.Event,
    period: float,
    on_tick: Callable[[], Awaitable[None]],
    *,
    wait_first: bool = False,
    on_error: Callable[[Exception], None] | None = None,
) -> None:
    """常驻周期循环:每 period 调 on_tick() 一次,睡眠可被 stop_event 中断。

    wait_first=True:先睡一轮再首次 tick(心跳——启动即空转一轮无意义,且首轮 DB 写
    前的连接未必就绪);False:立即首轮(log_retention/idle 首轮无副作用,尽快干活)。
    on_error:单轮 tick 异常回调(记日志继续);为 None 则异常向上抛、循环终止。
    CancelledError 直接退出(不吞,让任务取消语义完整)。"""
    if wait_first:
        await _interruptible_sleep(stop_event, period)
    while not stop_event.is_set():
        try:
            await on_tick()
        except Exception as e:
            if on_error is None:
                raise
            on_error(e)
        await _interruptible_sleep(stop_event, period)
