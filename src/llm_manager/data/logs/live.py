"""日志 live 层:level 推断、LogLine/_Session、内存 live 集、捕获入口、广播订阅
(自 logs 单文件拆出,2026-08-14)。模块级单例语义见 AGENTS.md §7。"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from dataclasses import dataclass

from llm_manager.data.logs.queries import log_end_session, log_start_session
from llm_manager.data.persistence import Db
from llm_manager.realtime import Broadcaster

logger = logging.getLogger(__name__)

_ERR = re.compile(r"error|fail|exception|traceback", re.IGNORECASE)
_OK = re.compile(r"listening|ready|started|server.*ok", re.IGNORECASE)

_SYS_LEVELS = {
    "DEBUG": "info",
    "INFO": "info",
    "WARNING": "warn",
    "ERROR": "error",
    "CRITICAL": "error",
}


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
    ts: float  # 墙钟(捕获时刻)
    stream: str  # "out" | "err" | "sys"
    level: str  # "info" | "ok" | "warn" | "error"
    text: str


@dataclass(slots=True)
class _Session:
    id: int
    type: str
    model_name: str | None
    bc: Broadcaster[LogLine]
    next_seq: int = 1


# ---- 模块级状态 ----
# 事件循环单线程。`_pending` 的读改写受 `_pending_lock` 保护:系统 logging handler
# 的 emit 可在任意线程调用 capture_system → _enqueue(追加 + seq 递增),与事件循环线程的
# flush(快照 + 清空)并发,无锁会丢行 / 重复 seq。`_system_session_id` 的读(任意线程)持锁,
# 写仅事件循环线程(lifespan 单线程设置/清除)。`_sessions`/`_alias_to_session` 的写只发生在
# 事件循环线程(模型路径);系统路径仅读 _system_session_id 与 _sessions.get
# (CPython GIL 下 dict.get 原子,读到旧会话只丢行不损坏)。
_db: Db | None = None
_sessions: dict[int, _Session] = {}
_alias_to_session: dict[str, int] = {}
_pending: list[
    tuple[int, int, float, str, str, str]
] = []  # (session_id, seq, ts, stream, level, text)
_system_session_id: int | None = None
_pending_lock = threading.Lock()
_mem_sid_seq: int = 0  # 未接线 DB 时的内存会话 id 分配(测试/启动早期)
BATCH_SIZE = 200


def init(db: Db) -> None:
    """接线 DB(幂等)。create_app 时调用;测试用 tmp DB。"""
    global _db
    _db = db


def reset() -> None:
    """测试隔离:清空全部状态(不写 DB)。"""
    global _system_session_id, _mem_sid_seq
    from llm_manager.data.logs import pipeline as _pipeline

    _sessions.clear()
    _alias_to_session.clear()
    _pending.clear()
    _system_session_id = None
    _pipeline._flush_chain = None
    _mem_sid_seq = 0


def start_session(
    type_: str, model_name: str | None = None, alias: str | None = None, start: float | None = None
) -> int:
    """开新会话(落库),登记广播器。alias→session 映射被新会话接管;
    type_="system" 的会话同时登记为当前系统会话。
    未接线 DB(_db 为 None,lifecycle 单测)→ 仅内存会话(不落库,与 end_session 对称)。"""
    global _system_session_id, _mem_sid_seq
    start = start if start is not None else time.time()
    if _db is not None:
        sid = log_start_session(_db, type_, model_name, alias, start)
    else:
        _mem_sid_seq += 1
        sid = _mem_sid_seq
    _sessions[sid] = _Session(sid, type_, model_name, Broadcaster())
    if model_name is not None:
        _alias_to_session[model_name] = sid
    if type_ == "system":
        _system_session_id = sid
    return sid


def end_session(session_id: int) -> None:
    """收口会话:落库 end_time;模型会话移除 alias 映射;系统会话清除当前登记。
    未接线 DB(_db 为 None,测试/启动早期)→ 仅内存收口。"""
    if _db is not None:
        log_end_session(_db, session_id, time.time())
    s = _sessions.get(session_id)
    if s is None:
        return
    _forget_session(s)


def _forget_session(s: _Session) -> None:
    """把会话从模块内存登记移除:广播器映射、alias 映射、系统当前登记。
    end_session 与 flush(会话 DB 行已被 retention 删除)共用。"""
    global _system_session_id
    _sessions.pop(s.id, None)
    if s.model_name is not None and _alias_to_session.get(s.model_name) == s.id:
        _alias_to_session.pop(s.model_name, None)
    if s.type == "system" and _system_session_id == s.id:
        _system_session_id = None


def current_system_session_id() -> int | None:
    """当前系统会话 id(任意线程安全);无 → None。系统会话状态的观测入口。"""
    with _pending_lock:
        return _system_session_id


def live_session_ids() -> set[int]:
    """公开只读访问器:当前内存中所有直播会话 id(flusher 仍在接收行的会话)。
    log_retention 用它排除直播会话(此前直接读私有 _sessions)。"""
    return set(_sessions)


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
    """系统日志入口(logging handler,任意线程)。无系统会话(启动早期)→ 丢弃。
    levelname 缺省时从行首 token 解析(形如 "WARNING disk full" 的文本格式)。"""
    if levelname is None:
        head = text.split(None, 1)
        levelname = head[0] if head else "INFO"
    with _pending_lock:
        sid = _system_session_id
    if sid is None:
        return
    _enqueue(sid, text, "sys", system_level(levelname), ts)


def _enqueue(session_id: int, text: str, stream: str, level: str, ts: float) -> None:
    s = _sessions.get(session_id)
    if s is None:
        return
    with _pending_lock:
        _pending.append((session_id, s.next_seq, ts, stream, level, text))
        s.next_seq += 1  # 多线程(系统 handler)可并发入队 → seq 递增必须持锁
        trigger = len(_pending) >= BATCH_SIZE
    if trigger:
        try:
            from llm_manager.data.logs import pipeline as _pipeline

            asyncio.get_running_loop().create_task(_pipeline.flush())
        except RuntimeError:
            pass  # 无运行 loop(测试/启动早期)→ 由 flush_loop 定时兜底


def subscribe(session_id: int):
    s = _sessions.get(session_id)
    return s.bc.subscribe() if s is not None else None


def unsubscribe(session_id: int, q) -> None:
    s = _sessions.get(session_id)
    if s is not None:
        s.bc.unsubscribe(q)


def resolve_session(alias: str) -> int | None:
    """alias → 当前内存中正在进行的会话 id;无进行中会话 → None。测试与后续调用方的观测入口。"""
    return _alias_to_session.get(alias)


def log_heartbeat_live(db: Db, now: float) -> int:
    """心跳(兼容再导出):live 集在本模块,SQL 在 queries——桥接。"""
    from llm_manager.data.logs.queries import log_heartbeat_live as _q

    return _q(db, now, live_ids=live_session_ids())
