"""Usage-row persistence. Plain functions taking a Db (conn + write_lock).
Single writer connection serialized by lock; reads concurrent under WAL."""
from __future__ import annotations

import logging
import math
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_manager.config import AppConfig, Pricing, PricingTier

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Db:
    conn: sqlite3.Connection
    write_lock: threading.Lock


def open_db(path: Path) -> Db:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS models (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_name TEXT UNIQUE NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS model_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id INTEGER NOT NULL,
            start_time REAL NOT NULL,
            end_time REAL NOT NULL,
            input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            cache_n INTEGER NOT NULL,
            prompt_n INTEGER NOT NULL,
            FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_model_requests_model_id ON model_requests(model_id);
        CREATE INDEX IF NOT EXISTS idx_model_requests_end ON model_requests(end_time);
        CREATE TABLE IF NOT EXISTS system_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS model_defs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            mode TEXT NOT NULL,
            port INTEGER NOT NULL,
            auto_start INTEGER NOT NULL DEFAULT 0,
            ord INTEGER NOT NULL DEFAULT 0,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS model_aliases (
            model_id INTEGER NOT NULL,
            alias TEXT NOT NULL,
            ord INTEGER NOT NULL,
            FOREIGN KEY (model_id) REFERENCES model_defs(id) ON DELETE CASCADE,
            UNIQUE(alias)
        );
        CREATE TABLE IF NOT EXISTS model_schemes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id INTEGER NOT NULL,
            config_source TEXT NOT NULL,
            required_devices TEXT NOT NULL DEFAULT '[]',
            memory_mb TEXT NOT NULL DEFAULT '{}',
            ord INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (model_id) REFERENCES model_defs(id) ON DELETE CASCADE,
            UNIQUE(model_id, config_source)
        );
        CREATE TABLE IF NOT EXISTS model_scripts (
            scheme_id INTEGER PRIMARY KEY,
            command TEXT NOT NULL,
            FOREIGN KEY (scheme_id) REFERENCES model_schemes(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS model_pricing (
            model_id INTEGER PRIMARY KEY,
            pricing_type TEXT NOT NULL DEFAULT 'tier',
            hourly_price REAL NOT NULL DEFAULT 0,
            support_cache INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY (model_id) REFERENCES model_defs(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS pricing_tiers (
            pricing_id INTEGER NOT NULL,
            tier_index INTEGER NOT NULL,
            min_input INTEGER, max_input INTEGER,
            min_output INTEGER, max_output INTEGER,
            input_price REAL, output_price REAL,
            cache_write_price REAL, cache_read_price REAL,
            FOREIGN KEY (pricing_id) REFERENCES model_pricing(model_id) ON DELETE CASCADE,
            PRIMARY KEY (pricing_id, tier_index)
        );
        CREATE TABLE IF NOT EXISTS model_runtime (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            model_id INTEGER NOT NULL,
            start_time REAL NOT NULL,
            end_time REAL,
            FOREIGN KEY (model_id) REFERENCES models(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_model_runtime_model ON model_runtime(model_id);
        CREATE INDEX IF NOT EXISTS idx_model_runtime_times ON model_runtime(start_time, end_time);
        CREATE TABLE IF NOT EXISTS log_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            type TEXT NOT NULL CHECK (type IN ('system','model')),
            model_name TEXT,
            alias TEXT,
            start_time REAL NOT NULL,
            end_time REAL
        );
        CREATE INDEX IF NOT EXISTS idx_log_sessions_start ON log_sessions(start_time);
        CREATE TABLE IF NOT EXISTS log_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL REFERENCES log_sessions(id) ON DELETE CASCADE,
            seq INTEGER NOT NULL,
            ts REAL NOT NULL,
            stream TEXT NOT NULL,
            level TEXT NOT NULL,
            text TEXT NOT NULL,
            UNIQUE (session_id, seq)
        );
        CREATE INDEX IF NOT EXISTS idx_log_lines_session ON log_lines(session_id, id);
    """)
    _migrate(conn)
    conn.commit()
    return Db(conn=conn, write_lock=threading.Lock())


def _migrate(conn: sqlite3.Connection) -> None:
    """Drop the obsolete ``ts`` column if present (Round-2 era). Option A folds the
    timestamp back into start_time/end_time (now wall-clock again, as in legacy). No-op
    on fresh DBs."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(model_requests)")}
    if "ts" in cols:
        # SQLite refuses DROP COLUMN while an index references it; drop the index first.
        conn.execute("DROP INDEX IF EXISTS idx_model_requests_ts")
        conn.execute("ALTER TABLE model_requests DROP COLUMN ts")
    # P4 回改:support_cache 从阶梯级上移到模型级(model_pricing)。
    # 旧库:model_pricing 无该列则补;pricing_tiers 有该列则删(SQLite ≥3.35 支持 DROP COLUMN)。
    mp_cols = {row[1] for row in conn.execute("PRAGMA table_info(model_pricing)")}
    if "support_cache" not in mp_cols:
        conn.execute("ALTER TABLE model_pricing ADD COLUMN support_cache INTEGER NOT NULL DEFAULT 0")
    pt_cols = {row[1] for row in conn.execute("PRAGMA table_info(pricing_tiers)")}
    if "support_cache" in pt_cols:
        conn.execute("ALTER TABLE pricing_tiers DROP COLUMN support_cache")


def _resolve_model_id_locked(db: Db, model_name: str) -> int:
    """Insert-or-return model id. Caller MUST already hold db.write_lock
    (threading.Lock is non-reentrant, so we cannot re-acquire here)."""
    row = db.conn.execute("SELECT id FROM models WHERE original_name = ?", (model_name,)).fetchone()
    if row:
        return row["id"]
    cur = db.conn.execute("INSERT INTO models (original_name) VALUES (?)", (model_name,))
    db.conn.commit()
    assert cur.lastrowid is not None  # AUTOINCREMENT PK always yields an int on INSERT
    return cur.lastrowid


def resolve_model_id(db: Db, model_name: str) -> int:
    """Public entry: takes the lock itself for standalone callers."""
    with db.write_lock:
        return _resolve_model_id_locked(db, model_name)


def record_usage(db: Db, model_name: str, start: float, end: float,
                 input_tokens: int, output_tokens: int, cache_n: int, prompt_n: int) -> None:
    with db.write_lock:
        mid = _resolve_model_id_locked(db, model_name)
        db.conn.execute(
            "INSERT INTO model_requests (model_id, start_time, end_time, input_tokens, output_tokens, cache_n, prompt_n) VALUES (?,?,?,?,?,?,?)",
            (mid, start, end, input_tokens, output_tokens, cache_n, prompt_n),
        )
        db.conn.commit()


def record_runtime_start(db: Db, model_name: str, start: float) -> None:
    """Begin a model-loaded billing session (model reached ROUTING). end_time stays NULL
    until record_runtime_end. Auto-creates the models row (a model can load before any request)."""
    with db.write_lock:
        mid = _resolve_model_id_locked(db, model_name)
        db.conn.execute(
            "INSERT INTO model_runtime (model_id, start_time, end_time) VALUES (?,?,NULL)",
            (mid, start),
        )
        db.conn.commit()


def record_runtime_end(db: Db, model_name: str, end: float) -> None:
    """Close the latest still-open session for the model (cooperative stop or crash)."""
    with db.write_lock:
        mid = _resolve_model_id_locked(db, model_name)
        db.conn.execute(
            "UPDATE model_runtime SET end_time=? WHERE id=("
            "SELECT id FROM model_runtime WHERE model_id=? AND end_time IS NULL ORDER BY id DESC LIMIT 1)",
            (end, mid),
        )
        db.conn.commit()


def fetch_usage(db: Db, model_name: str, start: float, end: float) -> list[sqlite3.Row]:
    return db.conn.execute(
        """SELECT r.* FROM model_requests r JOIN models m ON r.model_id = m.id
           WHERE m.original_name = ? AND r.end_time >= ? AND r.end_time <= ? ORDER BY r.end_time""",
        (model_name, start, end),
    ).fetchall()


@dataclass(frozen=True, slots=True)
class UsageSeries:
    buckets: list[float]            # bucket-start wall-clock epochs (the time axis)
    models: dict[str, list[float]]  # model → value per bucket (tokens 或 元,0-filled)
    total: list[float]              # value per bucket summed across models


def _bucket_axis(start_ts: float, end_ts: float, bucket_seconds: int) -> tuple[float, list[float]]:
    """时钟对齐桶轴:(first_bucket_start, [buckets])。空窗/非正桶 → (0.0, []).

    Buckets are **absolute** (clock-aligned to multiples of ``bucket_seconds``), not
    relative to the window start — so a request's bucket is fixed and a sliding live
    window scrolls the chart instead of reshaping it. Alignment to LOCAL boundaries
    (e.g. local midnight for daily) via the TZ offset.
    """
    if end_ts <= start_ts or bucket_seconds <= 0:
        return 0.0, []
    offset = (-time.localtime().tm_gmtoff) % bucket_seconds
    first = float(math.floor((start_ts - offset) / bucket_seconds) * bucket_seconds + offset)
    n = max(1, math.ceil((end_ts - first) / bucket_seconds))
    return first, [first + i * bucket_seconds for i in range(n)]


def usage_series(db: Db, *, start_ts: float, end_ts: float, bucket_seconds: int) -> UsageSeries:
    """Aggregate token consumption (input + output) per model + total, bucketed by wall-clock
    end_time (the request's completion timestamp — when usage is recorded).

    Buckets are **absolute** (clock-aligned to multiples of ``bucket_seconds``), not relative
    to the window start — so a request's bucket is fixed and a sliding live window scrolls
    the chart instead of reshaping it. Returns the full bucket axis 0-filled for continuity.
    ``tokens = input + output``.
    """
    first, buckets = _bucket_axis(start_ts, end_ts, bucket_seconds)
    n = len(buckets)
    if not buckets:
        return UsageSeries(buckets=[], models={}, total=[])

    rows = db.conn.execute(
        """SELECT m.original_name AS model,
                  CAST((r.end_time - :offset) / :bucket AS INTEGER) * :bucket + :offset AS bucket,
                  SUM(r.input_tokens + r.output_tokens) AS tokens
           FROM model_requests r JOIN models m ON r.model_id = m.id
           WHERE r.end_time >= :start AND r.end_time < :end
           GROUP BY m.original_name, bucket""",
        {"start": start_ts, "end": end_ts, "bucket": bucket_seconds, "offset": (-time.localtime().tm_gmtoff) % bucket_seconds},
    ).fetchall()

    models: dict[str, list[float]] = {}
    total: list[float] = [0.0] * n
    for row in rows:
        idx = int((row["bucket"] - first) // bucket_seconds)
        if 0 <= idx < n:
            tokens = int(row["tokens"])
            models.setdefault(row["model"], [0.0] * n)[idx] = tokens
            total[idx] += tokens
    return UsageSeries(buckets=buckets, models=models, total=total)


@dataclass(frozen=True, slots=True)
class UsageSummary:
    input_tokens: int
    output_tokens: int
    cache_hit: int       # SUM(cache_n)
    cache_miss: int      # SUM(prompt_n)
    hit_rate: float
    request_count: int


def usage_summary(db: Db, *, start_ts: float, end_ts: float) -> UsageSummary:
    """Aggregate token usage over the half-open window [start_ts, end_ts) by wall-clock
    end_time. Empty window → zeros (hit_rate 0.0)."""
    row = db.conn.execute(
        """SELECT COALESCE(SUM(input_tokens), 0) AS s_in,
                  COALESCE(SUM(output_tokens), 0) AS s_out,
                  COALESCE(SUM(cache_n), 0) AS s_cache,
                  COALESCE(SUM(prompt_n), 0) AS s_miss,
                  COUNT(*) AS n
           FROM model_requests
           WHERE end_time >= ? AND end_time < ?""",
        (start_ts, end_ts),
    ).fetchone()
    cache_hit = int(row["s_cache"])
    cache_miss = int(row["s_miss"])
    denom = cache_hit + cache_miss
    return UsageSummary(
        input_tokens=int(row["s_in"]),
        output_tokens=int(row["s_out"]),
        cache_hit=cache_hit,
        cache_miss=cache_miss,
        hit_rate=cache_hit / denom if denom else 0.0,
        request_count=int(row["n"]),
    )


@dataclass(frozen=True, slots=True)
class ByModelRow:
    model: str
    input_tokens: int
    output_tokens: int
    cache_n: int
    request_count: int
    hit_rate: float
    share: float
    latency_ms: float       # AVG(end_time - start_time) * 1000


def usage_by_model(db: Db, *, start_ts: float, end_ts: float) -> list[ByModelRow]:
    """Per-model aggregates over [start_ts, end_ts), ordered by input_tokens desc.
    share = model input / total input (0.0 when no input). latency_ms = mean wall-clock
    request duration in ms."""
    rows = db.conn.execute(
        """SELECT m.original_name AS model,
                  SUM(r.input_tokens) AS s_in,
                  SUM(r.output_tokens) AS s_out,
                  SUM(r.cache_n) AS s_cache,
                  SUM(r.prompt_n) AS s_miss,
                  COUNT(*) AS n,
                  AVG(r.end_time - r.start_time) AS s_lat
           FROM model_requests r JOIN models m ON r.model_id = m.id
           WHERE r.end_time >= ? AND r.end_time < ?
           GROUP BY m.original_name
           ORDER BY s_in DESC""",
        (start_ts, end_ts),
    ).fetchall()
    total_in = sum(int(r["s_in"]) for r in rows)
    out: list[ByModelRow] = []
    for r in rows:
        cache_hit = int(r["s_cache"])
        cache_miss = int(r["s_miss"])
        denom = cache_hit + cache_miss
        out.append(ByModelRow(
            model=r["model"],
            input_tokens=int(r["s_in"]),
            output_tokens=int(r["s_out"]),
            cache_n=cache_hit,
            request_count=int(r["n"]),
            hit_rate=cache_hit / denom if denom else 0.0,
            share=int(r["s_in"]) / total_in if total_in else 0.0,
            latency_ms=float(r["s_lat"] or 0) * 1000,
        ))
    return out


def _tier_matches(t: "PricingTier", inp: int, out: int) -> bool:  # type: ignore[name-defined]
    """First-tier-wins window match (legacy semantics): min=0 closed, else open;
    max None/negative = unbounded."""
    lo_i = 0 if (t.min_input is None or t.min_input < 0) else t.min_input
    hi_i = math.inf if (t.max_input is None or t.max_input < 0) else t.max_input
    i_ok = (inp >= lo_i) if lo_i == 0 else (inp > lo_i)
    lo_o = 0 if (t.min_output is None or t.min_output < 0) else t.min_output
    hi_o = math.inf if (t.max_output is None or t.max_output < 0) else t.max_output
    o_ok = (out >= lo_o) if lo_o == 0 else (out > lo_o)
    return i_ok and inp <= hi_i and o_ok and out <= hi_o


def tier_cost(pricing: "Pricing", input_t: int, output_t: int, cache_n: int, prompt_n: int) -> float:  # type: ignore[name-defined]
    """Per-request tier cost in yuan. First matching tier wins; no match → 0.
    Cache formula (legacy): cache_n×read + prompt_n×(input+write) + output×output.
    support_cache 是模型级开关(pricing.support_cache),控制缓存计费是否生效。"""
    if pricing.pricing_type != "tier" or not pricing.tiers:
        return 0.0
    for t in pricing.tiers:
        if not _tier_matches(t, input_t, output_t):
            continue
        if pricing.support_cache:
            raw = cache_n * t.cache_read_price + prompt_n * (t.input_price + t.cache_write_price) + output_t * t.output_price
        else:
            raw = input_t * t.input_price + output_t * t.output_price
        return raw / 1_000_000
    return 0.0


@dataclass(frozen=True, slots=True)
class CostByModel:
    model: str
    pricing_type: str
    cost: float


@dataclass(frozen=True, slots=True)
class CostSummary:
    total_cost: float
    by_model: list[CostByModel]


def usage_cost(
    db: Db,
    cfg: "AppConfig",  # type: ignore[name-defined]
    *,
    start_ts: float,
    end_ts: float,
    now: float | None = None,
) -> CostSummary:
    """Aggregate cost (yuan) over [start_ts, end_ts). tier 模型逐请求 tier_cost;
    hourly 模型按 model_runtime 与窗口重叠秒 × hourly_price/3600。免费/无数据模型省略。

    两套独立数据源:tier 走 model_requests(按 end_time 落窗);hourly 走
    model_runtime(按与窗口的重叠时长)。end_time IS NULL 的运行段用 now 收口。"""
    now_ts = now if now is not None else time.time()
    acc: dict[str, float] = {}

    # tier:逐请求计费,按 end_time 落入窗口
    tier_names = {n for n, m in cfg.models.items() if m.pricing.pricing_type == "tier"}
    if tier_names:
        rows = db.conn.execute(
            "SELECT m.original_name AS model, r.input_tokens, r.output_tokens, r.cache_n, r.prompt_n "
            "FROM model_requests r JOIN models m ON r.model_id=m.id "
            "WHERE r.end_time>=? AND r.end_time<?",
            (start_ts, end_ts),
        ).fetchall()
        for row in rows:
            mc = cfg.models.get(row["model"])
            if mc is None or mc.pricing.pricing_type != "tier":
                continue
            c = tier_cost(
                mc.pricing,
                int(row["input_tokens"]),
                int(row["output_tokens"]),
                int(row["cache_n"]),
                int(row["prompt_n"]),
            )
            if c:
                acc[row["model"]] = acc.get(row["model"], 0.0) + c

    # hourly:运行段与窗口的重叠时长 × 单价/3600
    hourly = {
        n: m.pricing.hourly_price
        for n, m in cfg.models.items()
        if m.pricing.pricing_type == "hourly" and m.pricing.hourly_price > 0
    }
    if hourly:
        rows = db.conn.execute(
            "SELECT m.original_name AS model, r.start_time, r.end_time "
            "FROM model_runtime r JOIN models m ON r.model_id=m.id "
            "WHERE r.start_time < ? AND (r.end_time IS NULL OR r.end_time > ?)",
            (end_ts, start_ts),
        ).fetchall()
        for row in rows:
            rate = hourly.get(row["model"])
            if not rate:
                continue
            sess_end = row["end_time"] if row["end_time"] is not None else now_ts
            overlap = max(0.0, min(end_ts, sess_end) - max(start_ts, row["start_time"]))
            if overlap > 0:
                acc[row["model"]] = acc.get(row["model"], 0.0) + overlap * rate / 3600.0

    ptype = {n: m.pricing.pricing_type for n, m in cfg.models.items()}
    by_model = [
        CostByModel(model=n, pricing_type=ptype.get(n, "tier"), cost=c)
        for n, c in acc.items()
        if c > 0
    ]
    by_model.sort(key=lambda x: x.cost, reverse=True)
    return CostSummary(total_cost=round(sum(x.cost for x in by_model), 6), by_model=by_model)


def usage_cost_series(
    db: Db,
    cfg: "AppConfig",  # type: ignore[name-defined]
    *,
    start_ts: float,
    end_ts: float,
    bucket_seconds: int,
    now: float | None = None,
) -> UsageSeries:
    """Bucketed cost series (元/桶),时钟对齐分桶(同 usage_series)。tier 成本按请求
    end_time 落桶;hourly 成本按运行段与各桶的重叠时长摊到桶。返回 UsageSeries 形
    (total/models 的值是元,非 token)。"""
    first, buckets = _bucket_axis(start_ts, end_ts, bucket_seconds)
    n = len(buckets)
    if not buckets:
        return UsageSeries(buckets=[], models={}, total=[])
    now_ts = now if now is not None else time.time()
    models: dict[str, list[float]] = {}
    total = [0.0] * n

    for name, m in cfg.models.items():
        if m.pricing.pricing_type == "tier":
            rows = db.conn.execute(
                "SELECT r.end_time, r.input_tokens, r.output_tokens, r.cache_n, r.prompt_n "
                "FROM model_requests r JOIN models mm ON r.model_id=mm.id "
                "WHERE mm.original_name=? AND r.end_time>=? AND r.end_time<?",
                (name, start_ts, end_ts),
            ).fetchall()
            for row in rows:
                c = tier_cost(
                    m.pricing,
                    int(row["input_tokens"]),
                    int(row["output_tokens"]),
                    int(row["cache_n"]),
                    int(row["prompt_n"]),
                )
                if c <= 0:
                    continue
                idx = int((row["end_time"] - first) // bucket_seconds)
                if 0 <= idx < n:
                    models.setdefault(name, [0.0] * n)[idx] += c
                    total[idx] += c
        elif m.pricing.pricing_type == "hourly" and m.pricing.hourly_price > 0:
            rate = m.pricing.hourly_price / 3600.0
            rows = db.conn.execute(
                "SELECT r.start_time, r.end_time FROM model_runtime r JOIN models mm ON r.model_id=mm.id "
                "WHERE mm.original_name=? AND r.start_time < ? AND (r.end_time IS NULL OR r.end_time > ?)",
                (name, end_ts, start_ts),
            ).fetchall()
            for row in rows:
                sess_end = row["end_time"] if row["end_time"] is not None else now_ts
                for i in range(n):
                    b_start = first + i * bucket_seconds
                    ov = max(0.0, min(b_start + bucket_seconds, sess_end) - max(b_start, row["start_time"]))
                    if ov > 0:
                        cost = ov * rate
                        models.setdefault(name, [0.0] * n)[i] += cost
                        total[i] += cost
    return UsageSeries(buckets=buckets, models=models, total=total)


@dataclass(frozen=True, slots=True)
class ModelDataStats:
    """单模型积累数据量(请求 + 运行段)。"""
    request_count: int
    has_runtime_data: bool


@dataclass(frozen=True, slots=True)
class StorageStats:
    """数据库存储统计(数据管理页)。size_bytes 由调用方传入(API 层从 resolved_db 取)。"""
    size_bytes: int | None
    total_requests: int
    total_models_with_data: int
    models_data: dict[str, ModelDataStats]


def storage_stats(db: Db, *, configured: set[str], size_bytes: int | None = None) -> StorageStats:
    """数据库存储统计(数据管理页)。models_data = 配置模型 ∪ 数据库模型的并集:
    配置但无记录的模型显示 0 请求/无运行段(与 legacy 表格一致)。孤立模型(仅在
    数据库,不在配置)同样列出。total_models_with_data = 请求 > 0 或有运行段的模型数。"""
    total_requests = int(db.conn.execute("SELECT COUNT(*) FROM model_requests").fetchone()[0])
    runtime_ids = {
        r["model_id"] for r in db.conn.execute("SELECT DISTINCT model_id FROM model_runtime")
    }
    stats: dict[str, tuple[int, bool]] = {}
    rows = db.conn.execute(
        "SELECT m.original_name AS name, m.id AS mid, "
        "(SELECT COUNT(*) FROM model_requests r WHERE r.model_id = m.id) AS rc "
        "FROM models m"
    ).fetchall()
    for row in rows:
        stats[row["name"]] = (int(row["rc"]), row["mid"] in runtime_ids)
    models_data: dict[str, ModelDataStats] = {}
    for name in sorted(set(configured) | set(stats)):
        rc, has_runtime = stats.get(name, (0, False))
        models_data[name] = ModelDataStats(request_count=rc, has_runtime_data=has_runtime)
    total_models_with_data = sum(
        1 for st in models_data.values() if st.request_count > 0 or st.has_runtime_data
    )
    return StorageStats(
        size_bytes=size_bytes,
        total_requests=total_requests,
        total_models_with_data=total_models_with_data,
        models_data=models_data,
    )


def orphaned_models(db: Db, configured: set[str]) -> list[str]:
    """孤立模型 = models 表存在但不在当前配置(primary_name 集合)中。升序。"""
    names = [r["original_name"] for r in db.conn.execute("SELECT original_name FROM models")]
    return sorted(n for n in names if n not in configured)


def delete_model_data(db: Db, model_name: str) -> bool:
    """删除模型全部积累数据(外键级联清 model_requests/model_runtime)。
    未知名称 → False。删除 commit 后自动 VACUUM + wal_checkpoint(TRUNCATE) 回收空间;
    VACUUM 失败仅 warning(legacy 同款;:memory: DB 的 VACUUM 亦被吞,不阻塞删除)。"""
    with db.write_lock:
        cur = db.conn.execute("DELETE FROM models WHERE original_name = ?", (model_name,))
        db.conn.commit()
        if cur.rowcount == 0:
            return False
    try:
        db.conn.execute("VACUUM")
        db.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    except Exception as e:  # noqa: BLE001 — VACUUM 失败不影响删除结果
        logger.warning("VACUUM 失败(不影响删除结果): %s", e)
    return True


# ---------------- log sessions / log lines ----------------


def log_start_session(db: Db, type_: str, model_name: str | None, alias: str | None, start: float) -> int:
    """开新日志会话(系统或模型);返回会话 id。"""
    with db.write_lock:
        cur = db.conn.execute(
            "INSERT INTO log_sessions (type, model_name, alias, start_time) VALUES (?,?,?,?)",
            (type_, model_name, alias, start),
        )
        db.conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid


def log_end_session(db: Db, session_id: int, end: float) -> None:
    """关闭会话:写入 end_time。"""
    with db.write_lock:
        db.conn.execute("UPDATE log_sessions SET end_time=? WHERE id=?", (end, session_id))
        db.conn.commit()


def log_insert_lines(db: Db, session_id: int, rows: list[tuple[int, float, str, str, str]]) -> list[int]:
    """批量插行。rows = [(seq, ts, stream, level, text), ...];返回全局行 id(RETURNING)。

    注意:CPython 的 executemany 不能用于带 RETURNING 的语句(sqlite3.InterfaceError),
    故用单条 execute + 多行 VALUES。语句参数数受 SQLITE_MAX_VARIABLE_NUMBER 限制
    (stock CPython 为 999 → 每语句 ≤166 行),因此按 150 行分块插入,累积各块行 id
    (全局自增,天然保持插入序);全程同一 write_lock、一次 commit。"""
    if not rows:
        return []
    chunk_size = 150
    ids: list[int] = []
    with db.write_lock:
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
        return ids


def log_sessions(db: Db, *, type_: str | None = None, model_name: str | None = None,
                 limit: int = 50, before_id: int | None = None) -> list[sqlite3.Row]:
    """会话列表倒序(id 降序)。line_count 一次 GROUP BY 算出;status 由 end_time 计算。
    before_id = id < before_id 的翻页。"""
    sql = ("SELECT s.*, COUNT(l.id) AS line_count, "
           "CASE WHEN s.end_time IS NULL THEN 'running' ELSE 'ended' END AS status "
           "FROM log_sessions s LEFT JOIN log_lines l ON l.session_id = s.id WHERE 1=1")
    args: list = []
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
               limit: int = 500) -> list[sqlite3.Row]:
    """行级 LIKE 检索,跨会话;返回匹配行(升序),含 session 归属。
    session_id 指定时限定单会话(日志页搜索跳转用)。SQLite LIKE 对 ASCII 大小写不敏感。"""
    sql = ("SELECT l.*, s.type AS session_type, s.model_name AS session_model "
           "FROM log_lines l JOIN log_sessions s ON s.id = l.session_id "
           "WHERE l.text LIKE '%' || ? || '%' COLLATE NOCASE")
    args: list = [q]
    if session_id is not None:
        sql += " AND l.session_id = ?"
        args.append(session_id)
    if type_ is not None:
        sql += " AND s.type = ?"
        args.append(type_)
    if model_name is not None:
        sql += " AND s.model_name = ?"
        args.append(model_name)
    if level is not None:
        sql += " AND l.level = ?"
        args.append(level)
    sql += " ORDER BY l.id LIMIT ?"
    args.append(max(1, min(limit, 500)))
    return db.conn.execute(sql, args).fetchall()
