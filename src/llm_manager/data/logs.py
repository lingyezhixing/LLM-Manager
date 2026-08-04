"""Per-model + system log capture 与日志会话/行的 SQL 存储层同模块(SQL 存储自
2026-08-03 起自 persistence 并入):DB-backed (single source of truth) with in-memory
broadcast for SSE. ``capture``/``capture_system`` enqueue lines; a batch flusher
(pending size or interval) persists to SQLite via log_insert_lines and
publishes the final DB rows (global ids) to the session broadcaster.

Sessions are opened by the runtime (system boot / model spawn) and closed on stop;
``end_session`` persists end_time and drops the alias→session mapping (late lines are
dropped). The system logging handler (data/log_handler.py) feeds ``capture_system``.
"""
from __future__ import annotations

import asyncio
import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass

from llm_manager.data.persistence import Db
from llm_manager.realtime import Broadcaster

logger = logging.getLogger(__name__)

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
_pending: list[tuple[int, int, float, str, str, str]] = []   # (session_id, seq, ts, stream, level, text)
_system_session_id: int | None = None
_pending_lock = threading.Lock()
_mem_sid_seq: int = 0   # 未接线 DB 时的内存会话 id 分配(测试/启动早期)
_flush_chain: asyncio.Task | None = None   # flush 串行链尾(见 flush 文档)
BATCH_SIZE = 200
FLUSH_INTERVAL = 1.0

# SQLITE_MAX_VARIABLE_NUMBER 999 → 每语句 ≤166 行,按 150 分块
INSERT_CHUNK_SIZE = 150


def init(db: Db) -> None:
    """接线 DB(幂等)。create_app 时调用;测试用 tmp DB。"""
    global _db
    _db = db


def reset() -> None:
    """测试隔离:清空全部状态(不写 DB)。"""
    global _system_session_id, _flush_chain, _mem_sid_seq
    _sessions.clear()
    _alias_to_session.clear()
    _pending.clear()
    _system_session_id = None
    _flush_chain = None
    _mem_sid_seq = 0


def start_session(type_: str, model_name: str | None = None,
                  alias: str | None = None, start: float | None = None) -> int:
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
        s.next_seq += 1   # 多线程(系统 handler)可并发入队 → seq 递增必须持锁
        trigger = len(_pending) >= BATCH_SIZE
    if trigger:
        try:
            asyncio.get_running_loop().create_task(flush())
        except RuntimeError:
            pass   # 无运行 loop(测试/启动早期)→ 由 flush_loop 定时兜底


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
        with _pending_lock:
            if not _pending:
                return
            if _db is None:          # 🔵2:未接线(测试/启动早期无库可写)→ 清空 pending 安全丢弃,避免无界增长
                _pending.clear()
                return
            batch = _pending[:]
            _pending.clear()
        by_session: dict[int, list[tuple[int, float, str, str, str]]] = {}
        for sid, seq, ts, stream, level, text in batch:
            by_session.setdefault(sid, []).append((seq, ts, stream, level, text))
        for sid, rows in by_session.items():
            try:
                ids = await asyncio.to_thread(log_insert_lines, _db, sid, rows)
            except Exception as e:  # noqa: BLE001 — 单会话落库失败不容许杀掉整批
                # 会话的 DB 行已被 retention 删除(或任何落库异常):该会话的剩余行
                # 已无法落库,丢弃它(停止接收新行)后继续落库其它会话——否则一个
                # 死会话会让 flush 抛 IntegrityError,flush_loop 只捕 Timeout/
                # Cancelled → 整个日志管线死亡、_pending 永久丢弃。
                s = _sessions.get(sid)
                if s is not None:
                    _forget_session(s)
                logger.warning("log flush: dropping dead session %d (insert failed: %s)", sid, e)
                continue
            s = _sessions.get(sid)
            if s is None:
                continue
            for line, lid in zip(rows, ids):
                s.bc.publish(LogLine(id=lid, ts=line[1], stream=line[2], level=line[3], text=line[4]))
    finally:
        if _flush_chain is me:
            _flush_chain = None


async def flush_loop(stop_event: asyncio.Event) -> None:
    """常驻 flush 任务(阈值 200 行或 1s,先到先 flush);退出前兜底清空剩余 pending。"""
    while not stop_event.is_set():
        try:
            if _pending:
                await flush()
            await asyncio.wait_for(stop_event.wait(), timeout=FLUSH_INTERVAL)
        except asyncio.TimeoutError:
            pass
        except asyncio.CancelledError:
            break
        except Exception:                  # 🔵2:兜底防未料异常静默杀掉日志管线(flush 内部已捕 insert 异常)
            logger.exception("flush_loop iteration failed; continuing")
    await flush()


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


# ---------------- log sessions / log lines (SQL 存储层,自 persistence 并入) ----------------


def log_start_session(db: Db, type_: str, model_name: str | None, alias: str | None, start: float) -> int:
    """开新日志会话(系统或模型);返回会话 id。会话 id 由 start_session 记入内存 _sessions
    (状态/心跳/retention 用);end_time 由心跳维持,不兼任「运行中」标识。"""
    with db.write_lock:
        cur = db.conn.execute(
            "INSERT INTO log_sessions (type, model_name, alias, start_time) VALUES (?,?,?,?)",
            (type_, model_name, alias, start),
        )
        db.conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid


def log_heartbeat_live(db: Db, now: float) -> int:
    """心跳:把所有进行中会话的 end_time 推到 now(从内存 live_session_ids 选会话)。

    由 heartbeat_loop 每 30s 调用。崩溃/强杀后 end_time 停在最后一次心跳(≈死亡时刻,
    误差 ≤ 心跳间隔);下次启动 live_session_ids 为空,残留会话天然 status=ended、
    end_time≈死亡时刻——无需启动收口。运行中状态由 live_session_ids(_sessions)表达,
    end_time 只管时间。"""
    ids = live_session_ids()
    if not ids:
        return 0
    placeholders = ",".join("?" * len(ids))
    with db.write_lock:
        cur = db.conn.execute(
            f"UPDATE log_sessions SET end_time=? WHERE id IN ({placeholders})", (now, *ids))
        n = cur.rowcount
        db.conn.commit()
        return n


def log_end_session(db: Db, session_id: int, end: float) -> None:
    """关闭会话:写入 end_time(精确)。心跳期间 end_time 已被推到接近 now;此处写最终值。"""
    with db.write_lock:
        db.conn.execute("UPDATE log_sessions SET end_time=? WHERE id=?", (end, session_id))
        db.conn.commit()


def log_session_exists(db: Db, session_id: int) -> bool:
    """会话行是否存在(读接口 404 校验用)。"""
    return db.conn.execute(
        "SELECT 1 FROM log_sessions WHERE id = ?", (session_id,)
    ).fetchone() is not None


def log_resolve_model_name(db: Db, alias_or_name: str) -> str | None:
    """按 alias 或 model_name 命中会话历史,返回最近一条的 model_name。

    配置里已删除模型的 alias 无法经 config.resolve_alias 解析,logs API 的
    model 参数回退到此处——让残留会话仍可按旧 alias/原名查看。无匹配 → None。"""
    row = db.conn.execute(
        "SELECT model_name FROM log_sessions WHERE alias = ? OR model_name = ? "
        "ORDER BY id DESC LIMIT 1",
        (alias_or_name, alias_or_name),
    ).fetchone()
    if row is None or row["model_name"] is None:
        return None
    return row["model_name"]


def log_insert_lines(db: Db, session_id: int, rows: list[tuple[int, float, str, str, str]]) -> list[int]:
    """批量插行。rows = [(seq, ts, stream, level, text), ...];返回全局行 id(RETURNING)。

    注意:CPython 的 executemany 不能用于带 RETURNING 的语句(sqlite3.InterfaceError),
    故用单条 execute + 多行 VALUES,按 INSERT_CHUNK_SIZE 行分块(参数数限制见常量注释);
    累积各块行 id(全局自增,天然保持插入序);全程同一 write_lock、一次 commit。任一块
    失败则 rollback 整体回滚(不落盘部分行)后重新抛出。"""
    if not rows:
        return []
    chunk_size = INSERT_CHUNK_SIZE
    ids: list[int] = []
    with db.write_lock:
        try:
            for i in range(0, len(rows), chunk_size):
                chunk = rows[i:i + chunk_size]
                sql = ("INSERT INTO log_lines (session_id, seq, ts, stream, level, text) VALUES "
                       + ",".join(["(?,?,?,?,?,?)"] * len(chunk)) + " RETURNING id")
                flat: list = []
                for r in chunk:
                    flat.append(session_id)
                    flat.extend(r)
                cur = db.conn.execute(sql, flat)
                ids.extend(row["id"] for row in cur.fetchall())
            db.conn.commit()
        except Exception:
            db.conn.rollback()
            raise
        return ids


def log_sessions(db: Db, *, type_: str | None = None, model_name: str | None = None,
                 limit: int = 50, before_id: int | None = None) -> list[sqlite3.Row]:
    """会话列表倒序(id 降序)。line_count 一次 GROUP BY 算出;status 由内存 live_session_ids
    计算(运行中 = 当前 _sessions 里的会话;end_time 已不兼任此标识,心跳会维持它到 now)。
    before_id = id < before_id 的翻页。"""
    live = live_session_ids()
    status_args: list[int] = list(live)
    if live:
        placeholders = ",".join("?" * len(live))
        status_sql = f"CASE WHEN s.id IN ({placeholders}) THEN 'running' ELSE 'ended' END"
    else:
        status_sql = "'ended'"   # 无运行中会话(如刚启动)→ 全 ended
    sql = ("SELECT s.*, COUNT(l.id) AS line_count, " + status_sql + " AS status "
           "FROM log_sessions s LEFT JOIN log_lines l ON l.session_id = s.id WHERE 1=1")
    args: list = status_args   # SELECT 里的 IN 占位符在 SQL 中最先出现
    if type_ is not None:
        sql += " AND s.type = ?"
        args.append(type_)
    if model_name is not None:
        sql += " AND s.model_name = ?"
        args.append(model_name)
    if before_id is not None:
        sql += " AND s.id < ?"
        args.append(before_id)
    sql += " GROUP BY s.id ORDER BY s.id DESC LIMIT ?"
    args.append(max(1, min(limit, 500)))
    return db.conn.execute(sql, args).fetchall()


def _log_lines_tail(db: Db, session_id: int, limit: int, level: str | None,
                    before_id: int | None = None) -> list[sqlite3.Row]:
    """会话内最近 limit 行(升序)。before_id 给定则限定 id < before_id(往前翻页)。"""
    sql = "SELECT * FROM log_lines WHERE session_id = ?"
    args: list = [session_id]
    if before_id is not None:
        sql += " AND id < ?"
        args.append(before_id)
    if level is not None:
        sql += " AND level = ?"
        args.append(level)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(limit, 5000)))
    rows = db.conn.execute(sql, args).fetchall()
    rows.reverse()
    return rows


def log_lines_backfill(db: Db, session_id: int, limit: int = 1500, level: str | None = None) -> list[sqlite3.Row]:
    """会话内最近 limit 行(升序)。"""
    return _log_lines_tail(db, session_id, limit, level)


def log_lines_before(db: Db, session_id: int, before_id: int, limit: int = 1500,
                     level: str | None = None) -> list[sqlite3.Row]:
    """id < before_id 的最近 limit 行(升序)——往前翻页。"""
    return _log_lines_tail(db, session_id, limit, level, before_id=before_id)


def log_search(db: Db, q: str, *, type_: str | None = None, model_name: str | None = None,
               session_id: int | None = None, level: str | None = None,
               limit: int = 500) -> tuple[int, list[sqlite3.Row]]:
    """行级 LIKE 检索,跨会话;返回 (total, rows)。total = 满足过滤条件的真总数(COUNT);
    rows = 按 id 升序 LIMIT 后的匹配行,含 session 归属。SQLite LIKE 对 ASCII
    大小写不敏感。limit 钳 1..5000(默认 500)。"""
    where = ["l.text LIKE '%' || ? || '%' COLLATE NOCASE"]
    args: list = [q]
    if session_id is not None:
        where.append("l.session_id = ?")
        args.append(session_id)
    if type_ is not None:
        where.append("s.type = ?")
        args.append(type_)
    if model_name is not None:
        where.append("s.model_name = ?")
        args.append(model_name)
    if level is not None:
        where.append("l.level = ?")
        args.append(level)
    cond = " AND ".join(where)
    total = db.conn.execute(
        f"SELECT COUNT(*) FROM log_lines l JOIN log_sessions s ON s.id = l.session_id WHERE {cond}",
        args).fetchone()[0]
    rows = db.conn.execute(
        f"SELECT l.*, s.type AS session_type, s.model_name AS session_model "
        f"FROM log_lines l JOIN log_sessions s ON s.id = l.session_id "
        f"WHERE {cond} ORDER BY l.id LIMIT ?",
        [*args, max(1, min(limit, 5000))]).fetchall()
    return int(total), rows


def log_cleanup(db: Db, days: int, count: int, now: float | None = None,
                live_session_ids: set[int] | None = None) -> tuple[int, int]:
    """保留规则:时间规则删 start_time < now-days 的会话;条数规则删最旧多余会话。
    两规则独立、同时生效、先到先清。返回 (删会话数, 删行数)。now 注入(可测)。

    live_session_ids = 模块内存中仍在运行的会话 id(flusher 正在接收行)——两规则都
    排除,绝不删除"正在直播"的会话行(belt-and-braces:行被删后 flush 落库 FK 失败,
    本模块 flush 已有兜底丢弃,但首选是不删)。

    IN 子句按 INSERT_CHUNK_SIZE id 分块(同 log_insert_lines:参数数限制见常量注释),
    行/会话数跨块累计;全程同一 write_lock、一次 commit,失败 rollback 整体回滚后
    重新抛出。"""
    now = now if now is not None else time.time()
    with db.write_lock:
        doomed: set[int] = set()
        cutoff = now - days * 86_400
        for r in db.conn.execute("SELECT id FROM log_sessions WHERE start_time < ?", (cutoff,)):
            doomed.add(r["id"])
        total = db.conn.execute("SELECT COUNT(*) FROM log_sessions").fetchone()[0]
        if total > count:
            excess = total - count
            for r in db.conn.execute("SELECT id FROM log_sessions ORDER BY id ASC LIMIT ?", (excess,)):
                doomed.add(r["id"])
        if live_session_ids:
            doomed.difference_update(live_session_ids)
        if not doomed:
            return 0, 0
        ids = list(doomed)
        chunk_size = INSERT_CHUNK_SIZE
        removed_l = 0
        removed_s = 0
        try:
            for i in range(0, len(ids), chunk_size):
                chunk = ids[i:i + chunk_size]
                ph = ",".join("?" * len(chunk))
                cur = db.conn.execute(f"DELETE FROM log_lines WHERE session_id IN ({ph})", chunk)
                removed_l += cur.rowcount
            for i in range(0, len(ids), chunk_size):
                chunk = ids[i:i + chunk_size]
                ph = ",".join("?" * len(chunk))
                cur = db.conn.execute(f"DELETE FROM log_sessions WHERE id IN ({ph})", chunk)
                removed_s += cur.rowcount
            db.conn.commit()
        except Exception:
            db.conn.rollback()
            raise
        return removed_s, removed_l


def log_counts(db: Db) -> tuple[int, int]:
    """(会话数, 行数) — DB 管理页统计。"""
    sessions = db.conn.execute("SELECT COUNT(*) FROM log_sessions").fetchone()[0]
    lines = db.conn.execute("SELECT COUNT(*) FROM log_lines").fetchone()[0]
    return sessions, lines
