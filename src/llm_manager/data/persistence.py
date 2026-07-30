"""Usage-row persistence. Plain functions taking a Db (conn + write_lock).
Single writer connection serialized by lock; reads concurrent under WAL."""
from __future__ import annotations

import math
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from llm_manager.config import AppConfig, Pricing, PricingTier


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
            FOREIGN KEY (model_id) REFERENCES model_defs(id) ON DELETE CASCADE
        );
        CREATE TABLE IF NOT EXISTS pricing_tiers (
            pricing_id INTEGER NOT NULL,
            tier_index INTEGER NOT NULL,
            min_input INTEGER, max_input INTEGER,
            min_output INTEGER, max_output INTEGER,
            input_price REAL, output_price REAL,
            support_cache INTEGER NOT NULL DEFAULT 0,
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
    models: dict[str, list[int]]    # model name → tokens per bucket (0-filled)
    total: list[int]                # tokens per bucket summed across all models


def usage_series(db: Db, *, start_ts: float, end_ts: float, bucket_seconds: int) -> UsageSeries:
    """Aggregate token consumption (input + output) per model + total, bucketed by wall-clock
    end_time (the request's completion timestamp — when usage is recorded).

    Buckets are **absolute** (clock-aligned to multiples of ``bucket_seconds``), not relative
    to the window start — so a request's bucket is fixed and a sliding live window scrolls
    the chart instead of reshaping it. Returns the full bucket axis 0-filled for continuity.
    ``tokens = input + output``.
    """
    if end_ts <= start_ts or bucket_seconds <= 0:
        return UsageSeries(buckets=[], models={}, total=[])

    # Align buckets to LOCAL boundaries (e.g. local midnight for daily) via the TZ offset,
    # so a 1-day bucket is a calendar day, not an epoch day (which would split at 8am local).
    offset = (-time.localtime().tm_gmtoff) % bucket_seconds
    first = float(math.floor((start_ts - offset) / bucket_seconds) * bucket_seconds + offset)
    n = max(1, math.ceil((end_ts - first) / bucket_seconds))
    buckets = [first + i * bucket_seconds for i in range(n)]
    rows = db.conn.execute(
        """SELECT m.original_name AS model,
                  CAST((r.end_time - :offset) / :bucket AS INTEGER) * :bucket + :offset AS bucket,
                  SUM(r.input_tokens + r.output_tokens) AS tokens
           FROM model_requests r JOIN models m ON r.model_id = m.id
           WHERE r.end_time >= :start AND r.end_time < :end
           GROUP BY m.original_name, bucket""",
        {"start": start_ts, "end": end_ts, "bucket": bucket_seconds, "offset": offset},
    ).fetchall()

    models: dict[str, list[int]] = {}
    total = [0] * n
    for row in rows:
        idx = int((row["bucket"] - first) // bucket_seconds)
        if 0 <= idx < n:
            tokens = int(row["tokens"])
            models.setdefault(row["model"], [0] * n)[idx] = tokens
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
    Cache formula (legacy): cache_n×read + prompt_n×(input+write) + output×output."""
    if pricing.pricing_type != "tier" or not pricing.tiers:
        return 0.0
    for t in pricing.tiers:
        if not _tier_matches(t, input_t, output_t):
            continue
        if t.support_cache:
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
