"""日志 flush 管道:pending 串行落库 + 行广播(自 logs 单文件拆出,2026-08-14)。
依赖 live 模块的状态(_pending/_sessions/_db/_forget_session);通过局部 import 避免循环依赖。"""

from __future__ import annotations

import asyncio
import logging

from llm_manager.data.logs import live as _live
from llm_manager.data.logs.queries import log_insert_lines

logger = logging.getLogger(__name__)

FLUSH_INTERVAL = 1.0
_flush_chain: asyncio.Task | None = None  # flush 串行链尾(见 flush 文档)


async def flush() -> None:
    """强制落库当前 pending(测试/关停用)。按 session 分组落库,落库后逐行广播(带 DB 全局 id)。

    并发 flush 严格串行(链式):先等链尾 flush 任务收尾、再自任新链尾——write_lock 非 FIFO,
    并行落库会把全局行 id 顺序打乱(与会话内 seq 脱节,backfill 呈现倒置历史),
    串行保证落库序 == 捕获序。"""
    global _flush_chain
    me = asyncio.current_task()
    while True:
        prev = _flush_chain
        if prev is None or prev is me:
            break
        await prev
    _flush_chain = me
    try:
        with _live._pending_lock:
            if not _live._pending:
                return
            if (
                _live._db is None
            ):  # 🔵2:未接线(测试/启动早期无库可写)→ 清空 pending 安全丢弃,避免无界增长
                _live._pending.clear()
                return
            batch = _live._pending[:]
            _live._pending.clear()
        by_session: dict[int, list[tuple[int, float, str, str, str]]] = {}
        for sid, seq, ts, stream, level, text in batch:
            by_session.setdefault(sid, []).append((seq, ts, stream, level, text))
        for sid, rows in by_session.items():
            try:
                ids = await asyncio.to_thread(log_insert_lines, _live._db, sid, rows)
            except Exception as e:  # noqa: BLE001 — 单会话落库失败不容许杀掉整批
                # 会话的 DB 行已被 retention 删除(或任何落库异常):该会话的剩余行
                # 已无法落库,丢弃它(停止接收新行)后继续落库其它会话——否则一个
                # 死会话会让 flush 抛 IntegrityError,flush_loop 只捕 Timeout/
                # Cancelled → 整个日志管线死亡、_pending 永久丢弃。
                s = _live._sessions.get(sid)
                if s is not None:
                    _live._forget_session(s)
                logger.warning("log flush: dropping dead session %d (insert failed: %s)", sid, e)
                continue
            s = _live._sessions.get(sid)
            if s is None:
                continue
            for line, lid in zip(rows, ids):
                s.bc.publish(
                    _live.LogLine(id=lid, ts=line[1], stream=line[2], level=line[3], text=line[4])
                )
    finally:
        if _flush_chain is me:
            _flush_chain = None


async def flush_loop(stop_event: asyncio.Event) -> None:
    """常驻 flush 任务(阈值 200 行或 1s,先到先 flush);退出前兜底清空剩余 pending。"""
    while not stop_event.is_set():
        try:
            if _live._pending:
                await flush()
            await asyncio.wait_for(stop_event.wait(), timeout=FLUSH_INTERVAL)
        except TimeoutError:
            pass
        except asyncio.CancelledError:
            break
        except Exception:  # 🔵2:兜底防未料异常静默杀掉日志管线(flush 内部已捕 insert 异常)
            logger.exception("flush_loop iteration failed; continuing")
    await flush()
