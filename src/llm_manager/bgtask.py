"""Fire-and-forget 后台任务注册表(强引用)。

asyncio 文档明示:仅被事件循环弱引用追踪的任务可能在完成前被 GC(官方警告),
长管线(spawn/stop/restart/flush/延迟退出)被回收即整体丢失。裸 create_task 一律
经 run() 调度:模块级集合强引用至完成,done 回调即时移除(防集合随未完成累积)。

任意层可引入(data/gateway/runtime 单向依赖中最底层,无业务依赖)。
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any

_background: set[asyncio.Task] = set()


def run(coro: Coroutine[Any, Any, Any]) -> asyncio.Task:
    """create_task + 强引用至完成;返回任务对象(caller 可另存).
    无运行 loop(测试/启动早期)→ 协程不会被消费,close 防 never-awaited 告警后重抛。"""
    try:
        task = asyncio.create_task(coro)
    except RuntimeError:
        coro.close()
        raise
    _background.add(task)
    task.add_done_callback(_background.discard)
    return task
