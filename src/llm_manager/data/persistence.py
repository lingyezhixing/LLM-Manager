"""Usage-row persistence. Plain functions taking a Db (conn + write_lock).
Single writer connection serialized by lock; reads concurrent under WAL."""
from __future__ import annotations

import math
import sqlite3
import threading
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
            ts REAL NOT NULL DEFAULT 0,
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
    """Add the wall-clock ``ts`` column + index to legacy model_requests tables
    (created before the usage time-series needed a displayable timestamp)."""
    cols = {row[1] for row in conn.execute("PRAGMA table_info(model_requests)")}
    if "ts" not in cols:
        conn.execute("ALTER TABLE model_requests ADD COLUMN ts REAL NOT NULL DEFAULT 0")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_model_requests_ts ON model_requests(ts)")


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


def record_usage(db: Db, model_name: str, ts: float, start: float, end: float,
                 input_tokens: int, output_tokens: int, cache_n: int, prompt_n: int) -> None:
    with db.write_lock:
        mid = _resolve_model_id_locked(db, model_name)
        db.conn.execute(
            "INSERT INTO model_requests (model_id, ts, start_time, end_time, input_tokens, output_tokens, cache_n, prompt_n) VALUES (?,?,?,?,?,?,?,?)",
            (mid, ts, start, end, input_tokens, output_tokens, cache_n, prompt_n),
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
    """Aggregate token consumption (input + output) per model + total, bucketed by wall-clock ts.

    Returns the full bucket axis ``[start, start+bucket, …)`` 0-filled, so the chart stays
    continuous even when a bucket has no requests. ``tokens = input + output``.
    """
    if end_ts <= start_ts or bucket_seconds <= 0:
        return UsageSeries(buckets=[], models={}, total=[])

    n = max(1, math.ceil((end_ts - start_ts) / bucket_seconds))
    buckets = [start_ts + i * bucket_seconds for i in range(n)]
    rows = db.conn.execute(
        """SELECT m.original_name AS model,
                  :start + CAST((r.ts - :start) / :bucket AS INTEGER) * :bucket AS bucket,
                  SUM(r.input_tokens + r.output_tokens) AS tokens
           FROM model_requests r JOIN models m ON r.model_id = m.id
           WHERE r.ts >= :start AND r.ts < :end
           GROUP BY m.original_name, bucket""",
        {"start": start_ts, "end": end_ts, "bucket": bucket_seconds},
    ).fetchall()

    models: dict[str, list[int]] = {}
    total = [0] * n
    for row in rows:
        idx = int((row["bucket"] - start_ts) // bucket_seconds)
        if 0 <= idx < n:
            tokens = int(row["tokens"])
            models.setdefault(row["model"], [0] * n)[idx] = tokens
            total[idx] += tokens
    return UsageSeries(buckets=buckets, models=models, total=total)
