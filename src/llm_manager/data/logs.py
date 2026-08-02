"""Per-model + system log capture: DB-backed (single source of truth) with in-memory
broadcast for SSE. ``capture``/``capture_system`` enqueue lines; a batch flusher
(pending size or interval) persists to SQLite via persistence.log_insert_lines and
publishes the final DB rows (global ids) to the session broadcaster.

Sessions are opened by the runtime (system boot / model spawn) and closed on stop;
``end_session`` persists end_time and drops the alias→session mapping (late lines are
dropped). The system logging handler (data/log_handler.py) feeds ``capture_system``.
"""
from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass

from llm_manager.data import persistence as _p
from llm_manager.realtime import Broadcaster

_ERR = re.compile(r"error|fail|exception|traceback", re.I)
_OK = re.compile(r"listening|ready|started|server.*ok", re.I)

_SYS_LEVELS = {"DEBUG": "info", "INFO": "info", "WARNING": "warn",
               "ERROR": "error", "CRITICAL": "error"}


def infer_level(text: str, stream: str) -> str:
    if stream == "err" and _ERR.search(text):
        return "error"
    if stream == "err":
        return "warn"
    if _OK.search(text):
        return "ok"
    return "info"


def system_level(levelname: str) -> str:
    """logging levelname → 4 级归一。"""
    return _SYS_LEVELS.get(levelname, "info")


@dataclass(frozen=True, slots=True)
class LogLine:
    id: int
    ts: float            # 墙钟(捕获时刻)
    stream: str          # "out" | "err" | "sys"
    level: str           # "info" | "ok" | "warn" | "error"
    text: str


@dataclass(slots=True)
class _Session:
    id: int
    type: str
    model_name: str | None
    alias: str | None
    start: float
    bc: Broadcaster[LogLine]
    next_seq: int = 1


# ---- 模块级状态(事件循环单线程,无需锁)----
_db: _p.Db | None = None
_sessions: dict[int, _Session] = {}
_alias_to_session: dict[str, int] = {}
_pending: list[tuple[int, int, float, str, str, str]] = []   # (session_id, seq, ts, stream, level, text)
_system_session_id: int | None = None
BATCH_SIZE = 200
FLUSH_INTERVAL = 1.0


def init(db: _p.Db) -> None:
    """接线 DB(幂等)。create_app 时调用;测试用 tmp DB。"""
    global _db
    _db = db


def reset() -> None:
    """测试隔离:清空全部状态(不写 DB)。"""
    global _system_session_id
    _sessions.clear()
    _alias_to_session.clear()
    _pending.clear()
    _system_session_id = None


def start_session(type_: str, model_name: str | None = None,
                  alias: str | None = None, start: float | None = None) -> int:
    """开新会话(落库),登记广播器。alias→session 映射被新会话接管;
    type_="system" 的会话同时登记为当前系统会话。"""
    global _system_session_id
    assert _db is not None, "logs.init(db) 未调用"
    start = start if start is not None else time.time()
    sid = _p.log_start_session(_db, type_, model_name, alias, start)
    _sessions[sid] = _Session(sid, type_, model_name, alias, start, Broadcaster())
    if model_name is not None:
        _alias_to_session[model_name] = sid
    if type_ == "system":
        _system_session_id = sid
    return sid


def end_session(session_id: int) -> None:
    """收口会话:落库 end_time;模型会话移除 alias 映射;系统会话清除当前登记;
    无订阅者的广播器移除。未接线 DB(_db 为 None,测试/启动早期)→ 仅内存收口。"""
    global _system_session_id
    if _db is not None:
        _p.log_end_session(_db, session_id, time.time())
    s = _sessions.pop(session_id, None)
    if s is None:
        return
    if s.model_name is not None and _alias_to_session.get(s.model_name) == session_id:
        _alias_to_session.pop(s.model_name, None)
    if s.type == "system" and _system_session_id == session_id:
        _system_session_id = None
    if s.bc.subscriber_count == 0:
        del s.bc  # 无订阅者 → 广播器随会话丢弃


def current_system_session_id() -> int | None:
    return _system_session_id


def start_system_session() -> int:
    global _system_session_id
    _system_session_id = start_session("system")
    return _system_session_id


def end_system_session() -> None:
    global _system_session_id
    if _system_session_id is not None:
        end_session(_system_session_id)
        _system_session_id = None


def capture(alias: str, line: str, stream: str) -> None:
    """模型日志入口(supervisor on_output)。无当前会话(已停止/未启动)→ 丢弃。"""
    sid = _alias_to_session.get(alias)
    if sid is None:
        return
    _enqueue(sid, line, stream, infer_level(line, stream), time.time())


def capture_system(text: str, ts: float, levelname: str | None = None) -> None:
    """系统日志入口(logging handler)。无系统会话(启动早期)→ 丢弃。
    levelname 缺省时从行首 token 解析(形如 "WARNING disk full" 的文本格式)。"""
    if _system_session_id is None:
        return
    if levelname is None:
        head = text.split(None, 1)
        levelname = head[0] if head else "INFO"
    _enqueue(_system_session_id, text, "sys", system_level(levelname), ts)


def _enqueue(session_id: int, text: str, stream: str, level: str, ts: float) -> None:
    s = _sessions.get(session_id)
    if s is None:
        return
    _pending.append((session_id, s.next_seq, ts, stream, level, text))
    s.next_seq += 1
    if len(_pending) >= BATCH_SIZE:
        try:
            asyncio.get_running_loop().create_task(flush())
        except RuntimeError:
            pass   # 无运行 loop(测试/启动早期)→ 由 flush_loop 定时兜底


async def flush() -> None:
    """强制落库当前 pending(测试/关停用)。按 session 分组落库,落库后逐行广播(带 DB 全局 id)。"""
    if not _pending:
        return
    assert _db is not None
    batch = _pending[:]
    _pending.clear()
    by_session: dict[int, list[tuple[int, float, str, str, str]]] = {}
    for sid, seq, ts, stream, level, text in batch:
        by_session.setdefault(sid, []).append((seq, ts, stream, level, text))
    for sid, rows in by_session.items():
        ids = await asyncio.to_thread(_p.log_insert_lines, _db, sid, rows)
        s = _sessions.get(sid)
        if s is None:
            continue
        for line, lid in zip(rows, ids):
            s.bc.publish(LogLine(id=lid, ts=line[1], stream=line[2], level=line[3], text=line[4]))


async def flush_loop(stop_event: asyncio.Event) -> None:
    """常驻 flush 任务(阈值 200 行或 1s,先到先 flush)。"""
    while not stop_event.is_set():
        try:
            if _pending:
                await flush()
            await asyncio.wait_for(stop_event.wait(), timeout=FLUSH_INTERVAL)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            break


def subscribe(session_id: int):
    s = _sessions.get(session_id)
    return s.bc.subscribe() if s is not None else None


def unsubscribe(session_id: int, q) -> None:
    s = _sessions.get(session_id)
    if s is not None:
        s.bc.unsubscribe(q)


def resolve_session(alias: str) -> int | None:
    """alias → 当前进行中会话 id(旧端点兼容用)。"""
    return _alias_to_session.get(alias)
