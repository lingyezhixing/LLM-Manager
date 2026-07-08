"""Usage-row persistence. Plain functions taking a Db (conn + write_lock).
Single writer connection serialized by lock; reads concurrent under WAL."""
from __future__ import annotations

import math
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path


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


def usage_by_model(db: Db, *, start_ts: float, end_ts: float) -> list[ByModelRow]:
    """Per-model aggregates over [start_ts, end_ts), ordered by input_tokens desc.
    share = model input / total input (0.0 when no input)."""
    rows = db.conn.execute(
        """SELECT m.original_name AS model,
                  SUM(r.input_tokens) AS s_in,
                  SUM(r.output_tokens) AS s_out,
                  SUM(r.cache_n) AS s_cache,
                  SUM(r.prompt_n) AS s_miss,
                  COUNT(*) AS n
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
        ))
    return out


@dataclass(frozen=True, slots=True)
class RequestRow:
    id: int
    model: str
    start_time: float
    end_time: float
    input_tokens: int
    output_tokens: int
    cache_n: int
    latency_ms: float


@dataclass(frozen=True, slots=True)
class FetchRequestsResult:
    rows: list[RequestRow]
    has_more: bool
    total: int


def fetch_requests(
    db: Db, *, start_ts: float, end_ts: float,
    model_name: str | None = None, limit: int = 50, before: int | None = None,
) -> FetchRequestsResult:
    """Paginated raw request rows over [start_ts, end_ts), newest first (id DESC).
    Cursor: ``before`` = return rows with id < before. has_more via fetch limit+1.
    total = COUNT with same WHERE (incl. model filter) for pager math."""
    where = "r.end_time >= ? AND r.end_time < ?"
    params: list = [start_ts, end_ts]
    if model_name is not None:
        where += " AND m.original_name = ?"
        params.append(model_name)
    if before is not None:
        where += " AND r.id < ?"
        params.append(before)

    total = db.conn.execute(
        f"""SELECT COUNT(*) FROM model_requests r JOIN models m ON r.model_id = m.id
            WHERE {where}""",
        params,
    ).fetchone()[0]

    fetch_n = max(1, limit) + 1
    raw = db.conn.execute(
        f"""SELECT r.id, m.original_name AS model, r.start_time, r.end_time,
                   r.input_tokens, r.output_tokens, r.cache_n
            FROM model_requests r JOIN models m ON r.model_id = m.id
            WHERE {where}
            ORDER BY r.id DESC
            LIMIT ?""",
        [*params, fetch_n],
    ).fetchall()
    has_more = len(raw) > limit
    rows = [
        RequestRow(
            id=int(r["id"]), model=r["model"],
            start_time=float(r["start_time"]), end_time=float(r["end_time"]),
            input_tokens=int(r["input_tokens"]), output_tokens=int(r["output_tokens"]),
            cache_n=int(r["cache_n"]),
            latency_ms=(float(r["end_time"]) - float(r["start_time"])) * 1000,
        )
        for r in raw[:limit]
    ]
    return FetchRequestsResult(rows=rows, has_more=has_more, total=total)
