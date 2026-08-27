"""运行段记录与 live 集管理(record_usage + runtime lifecycle)。

本文档的 segment=计费运行段(model_runtime 行,模型达到 ROUTING 起止);logs 包的 session=日志会话(log_sessions 行,进程/模型生命周期起止)。两概念随模型启停一一对应创建,但表独立、id 独立、语义独立(计费 vs 日志)。

运行段语义(不变量 6 的 usage 侧):end_time 只管时间戳字段,不标识运行中;
内存态 live 集(_live_segments)才是「进行中」的权威来源。心跳持续推 end_time,
崩溃后 end_time 停在最后一次心跳(≈死亡时刻),但 live 集随进程消失。
"""

from llm_manager.data.persistence import Db, push_end_times


def _resolve_model_id_locked(db: Db, model_name: str) -> int:
    """取或插 model id。调用方必须已持有 db.write_lock
    (threading.Lock 不可重入,此处不能再取)。"""
    row = db.conn.execute("SELECT id FROM models WHERE original_name = ?", (model_name,)).fetchone()
    if row:
        return row["id"]
    cur = db.conn.execute("INSERT INTO models (original_name) VALUES (?)", (model_name,))
    db.conn.commit()
    assert cur.lastrowid is not None  # AUTOINCREMENT 主键在 INSERT 时总会产生 int
    return cur.lastrowid


def resolve_model_id(db: Db, model_name: str) -> int:
    """公共入口:独立调用方自行取锁。"""
    with db.write_lock:
        return _resolve_model_id_locked(db, model_name)


def record_usage(
    db: Db,
    model_name: str,
    start: float,
    end: float,
    input_tokens: int,
    output_tokens: int,
    cache_n: int,
    prompt_n: int,
    source: str = "local",
) -> None:
    with db.write_lock:
        mid = _resolve_model_id_locked(db, model_name)
        db.conn.execute(
            "INSERT INTO model_requests (model_id, start_time, end_time, input_tokens, output_tokens, cache_n, prompt_n, source) VALUES (?,?,?,?,?,?,?,?)",
            (mid, start, end, input_tokens, output_tokens, cache_n, prompt_n, source),
        )
        db.conn.commit()


# 进行中(已 start 未 end)的运行段 id——内存态,与 logs._sessions 对称。
# 心跳据此选段写 end_time;record_runtime_end 据此 discard;崩溃随进程消失。
# 不依赖 end_time IS NULL 定位运行段(心跳会持续把运行段 end_time 推到 now)。
_live_segments: set[int] = set()


def live_segment_ids() -> set[int]:
    """公开只读:当前内存中所有进行中运行段 id(心跳/lifecycle 用)。"""
    return set(_live_segments)


def record_runtime_start(db: Db, model_name: str, start: float) -> int:
    """开启一次模型加载计费会话(模型达到 ROUTING);返回段 id。
    自动创建 models 行(模型可在任何请求前加载)。段 id 记入
    _live_segments(心跳/关闭用);end_time 由心跳维持,不兼任「运行中」标识。"""
    with db.write_lock:
        mid = _resolve_model_id_locked(db, model_name)
        cur = db.conn.execute(
            "INSERT INTO model_runtime (model_id, start_time, end_time) VALUES (?,?,NULL)",
            (mid, start),
        )
        db.conn.commit()
        assert cur.lastrowid is not None
        sid = cur.lastrowid
        _live_segments.add(sid)
        return sid


def runtime_heartbeat_live(db: Db, now: float) -> int:
    """心跳:把所有进行中运行段的 end_time 推到 now(从内存 _live_segments 选段)。

    由 heartbeat_loop 每 30s 调用。崩溃/强杀后 end_time 停在最后一次心跳(≈死亡时刻,
    误差 ≤ 心跳间隔)——usage_cost 直接读 end_time,不再按 now 持续计费、不含停机时长,
    也无需启动收口。"""
    return push_end_times(db, "model_runtime", live_segment_ids(), now)


def record_runtime_end(db: Db, segment_id: int, end: float) -> None:
    """按 id 关闭计费会话(模型停止/崩溃;lifecycle 持 alias→segment_id 映射)。
    幂等:segment_id 不在 _live_segments(已关/未知)→ no-op。"""
    if segment_id not in _live_segments:
        return
    with db.write_lock:
        db.conn.execute("UPDATE model_runtime SET end_time=? WHERE id=?", (end, segment_id))
        db.conn.commit()
    _live_segments.discard(segment_id)
