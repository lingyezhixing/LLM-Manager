"""日志会话/行的纯 SQL 存储层。无模块级可变状态。"""

from __future__ import annotations

import sqlite3
import time

from llm_manager.data.persistence import Db

# SQLITE_MAX_VARIABLE_NUMBER 999 → 每语句 ≤166 行,按 150 分块
INSERT_CHUNK_SIZE = 150


def log_start_session(
    db: Db, type_: str, model_name: str | None, alias: str | None, start: float
) -> int:
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


def log_heartbeat_live(db: Db, now: float, live_ids: set[int]) -> int:
    """心跳:把所有进行中会话的 end_time 推到 now(接收 live_ids 参数)。

    由 heartbeat_loop 每 30s 调用。崩溃/强杀后 end_time 停在最后一次心跳(≈死亡时刻,
    误差 ≤ 心跳间隔);下次启动 live_session_ids 为空,残留会话天然 status=ended、
    end_time≈死亡时刻——无需启动收口。运行中状态由 live_session_ids(_sessions)表达,
    end_time 只管时间。

    live_ids = 内存中仍在运行的会话 id(由调用方传入)。
    """
    from llm_manager.data.persistence import push_end_times

    return push_end_times(db, "log_sessions", live_ids, now)


def log_end_session(db: Db, session_id: int, end: float) -> None:
    """关闭会话:写入 end_time(精确)。心跳期间 end_time 已被推到接近 now;此处写最终值。"""
    with db.write_lock:
        db.conn.execute("UPDATE log_sessions SET end_time=? WHERE id=?", (end, session_id))
        db.conn.commit()


def _delete_sessions_locked(db: Db, terms: set[str]) -> int:
    """DELETE log_sessions 中 model_name 或 alias ∈ terms。caller 持 write_lock,不 commit。"""
    if not terms:
        return 0
    ph = ",".join("?" * len(terms))
    cur = db.conn.execute(
        f"DELETE FROM log_sessions WHERE model_name IN ({ph}) OR alias IN ({ph})",
        (*terms, *terms),
    )
    return cur.rowcount


def delete_model_sessions(db: Db, model_name: str, aliases: tuple[str, ...]) -> int:
    """删模型定义时连带删除其全部日志会话(级联 log_lines,ON DELETE CASCADE)。

    设计:删定义 = 删日志 + 保留请求记录(请求记录成为孤立模型,由数据管理页的
    孤立模型清理,见 persistence.orphaned_models)。匹配 model_name(恒为 primary)
    + alias(aliases[0] 或旧数据变体),belt-and-braces。返回删除的会话数。"""
    with db.write_lock:
        n = _delete_sessions_locked(db, {model_name, *aliases})
        db.conn.commit()
        return n


def log_session_exists(db: Db, session_id: int) -> bool:
    """会话行是否存在(读接口 404 校验用)。"""
    return (
        db.conn.execute("SELECT 1 FROM log_sessions WHERE id = ?", (session_id,)).fetchone()
        is not None
    )


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


def log_insert_lines(
    db: Db, session_id: int, rows: list[tuple[int, float, str, str, str]]
) -> list[int]:
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
                chunk = rows[i : i + chunk_size]
                sql = (
                    "INSERT INTO log_lines (session_id, seq, ts, stream, level, text) VALUES "
                    + ",".join(["(?,?,?,?,?,?)"] * len(chunk))
                    + " RETURNING id"
                )
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


def log_sessions(
    db: Db,
    *,
    type_: str | None = None,
    model_name: str | None = None,
    limit: int = 50,
    before_id: int | None = None,
    live_ids: set[int] | None = None,
) -> list[sqlite3.Row]:
    """会话列表倒序(id 降序)。line_count 一次 GROUP BY 算出;status 由传入的 live_ids
    计算(运行中 = live_ids 里的会话;end_time 已不兼任此标识,心跳会维持它到 now)。
    before_id = id < before_id 的翻页。

    live_ids = 内存中仍在运行的会话 id(由调用方传入)。

    子查询先取页面会话再聚合:原「全表 GROUP BY → ORDER BY → LIMIT」在行数大时
    (日志保 N 天积累)每次列表页全量聚合扫描;改后 LIMIT 先作用于 log_sessions
    (主键 id 逆序,代价 O(页)),LEFT JOIN 仅聚合页面内会话。"""
    live = live_ids or set()
    status_args: list[int] = list(live)
    if live:
        placeholders = ",".join("?" * len(live))
        status_sql = f"CASE WHEN s.id IN ({placeholders}) THEN 'running' ELSE 'ended' END"
    else:
        status_sql = "'ended'"  # 无运行中会话(如刚启动)→ 全 ended
    sql = (
        "SELECT s.*, COUNT(l.id) AS line_count, " + status_sql + " AS status "
        "FROM (SELECT * FROM log_sessions WHERE 1=1"
    )
    args: list = status_args  # SELECT 里的 IN 占位符在 SQL 中最先出现
    if type_ is not None:
        sql += " AND type = ?"
        args.append(type_)
    if model_name is not None:
        sql += " AND model_name = ?"
        args.append(model_name)
    if before_id is not None:
        sql += " AND id < ?"
        args.append(before_id)
    sql += " ORDER BY id DESC LIMIT ?)"
    sql += " s LEFT JOIN log_lines l ON l.session_id = s.id GROUP BY s.id ORDER BY s.id DESC"
    args.append(max(1, min(limit, 500)))
    return db.conn.execute(sql, args).fetchall()


def _log_lines_tail(
    db: Db, session_id: int, limit: int, level: str | None, before_id: int | None = None
) -> list[sqlite3.Row]:
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


def log_lines_backfill(
    db: Db, session_id: int, limit: int = 1500, level: str | None = None
) -> list[sqlite3.Row]:
    """会话内最近 limit 行(升序)。"""
    return _log_lines_tail(db, session_id, limit, level)


def log_lines_before(
    db: Db, session_id: int, before_id: int, limit: int = 1500, level: str | None = None
) -> list[sqlite3.Row]:
    """id < before_id 的最近 limit 行(升序)——往前翻页。"""
    return _log_lines_tail(db, session_id, limit, level, before_id=before_id)


def log_search(
    db: Db,
    q: str,
    *,
    type_: str | None = None,
    model_name: str | None = None,
    session_id: int | None = None,
    level: str | None = None,
    limit: int = 500,
) -> tuple[int, list[sqlite3.Row]]:
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
        args,
    ).fetchone()[0]
    rows = db.conn.execute(
        f"SELECT l.*, s.type AS session_type, s.model_name AS session_model "
        f"FROM log_lines l JOIN log_sessions s ON s.id = l.session_id "
        f"WHERE {cond} ORDER BY l.id LIMIT ?",
        [*args, max(1, min(limit, 5000))],
    ).fetchall()
    return int(total), rows


def log_cleanup(
    db: Db,
    days: int,
    count: int,
    now: float | None = None,
    live_ids: set[int] | None = None,
) -> tuple[int, int]:
    """保留规则:时间规则删 start_time < now-days 的会话;条数规则删最旧多余会话。
    两规则独立、同时生效、先到先清。返回 (删会话数, 删行数)。now 注入(可测)。

    live_ids = 模块内存中仍在运行的会话 id(flusher 正在接收行)——两规则都
    排除,绝不删除"正在直播"的会话行(belt-and-braces:行被删后 flush 落库 FK 失败,
    本模块 flush 已有兜底丢弃,但首选是不删)。

    IN 子句按 INSERT_CHUNK_SIZE id 分块(同 log_insert_lines:参数数限制见常量注释),
    行/会话数跨块累计;全程同一 write_lock、一次 commit,失败 rollback 整体回滚后
    重新抛出。"""
    now_ts = now if now is not None else time.time()  # 参数语义注入,内部用别名防遮蔽
    with db.write_lock:
        doomed: set[int] = set()
        cutoff = now_ts - days * 86_400
        for r in db.conn.execute("SELECT id FROM log_sessions WHERE start_time < ?", (cutoff,)):
            doomed.add(r["id"])
        total = db.conn.execute("SELECT COUNT(*) FROM log_sessions").fetchone()[0]
        if total > count:
            excess = total - count
            for r in db.conn.execute(
                "SELECT id FROM log_sessions ORDER BY id ASC LIMIT ?", (excess,)
            ):
                doomed.add(r["id"])
        if live_ids:
            doomed.difference_update(live_ids)
        if not doomed:
            return 0, 0
        ids = list(doomed)
        chunk_size = INSERT_CHUNK_SIZE
        removed_l = 0
        removed_s = 0
        try:
            for i in range(0, len(ids), chunk_size):
                chunk = ids[i : i + chunk_size]
                ph = ",".join("?" * len(chunk))
                cur = db.conn.execute(f"DELETE FROM log_lines WHERE session_id IN ({ph})", chunk)
                removed_l += cur.rowcount
            for i in range(0, len(ids), chunk_size):
                chunk = ids[i : i + chunk_size]
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
