"""SqliteStore: single shared connection (busy_timeout + WAL + foreign_keys),
schema bootstrap via CREATE TABLE IF NOT EXISTS (no migration — fresh DB at data/).

Tables kept are the backend-core write targets only (model_requests + sessions).
Billing tables are deferred until billing is redesigned (spec §11).
"""

from __future__ import annotations

import pathlib
import sqlite3
import threading

_SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    original_name TEXT UNIQUE NOT NULL
);
CREATE TABLE IF NOT EXISTS model_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id INTEGER NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    start_time REAL NOT NULL,
    end_time REAL NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cache_n INTEGER NOT NULL,
    prompt_n INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS program_runtime (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    start_time REAL NOT NULL,
    end_time REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS model_runtime (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_id INTEGER NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    start_time REAL NOT NULL,
    end_time REAL NOT NULL
);
"""


class SqliteStore:
    """Owns one sqlite3 connection guarded by a re-entrant lock.

    A single shared connection + busy_timeout replaces the old per-thread
    thread-local connections that leaked across worker threads.
    """

    def __init__(self, db_path: pathlib.Path) -> None:
        self.db_path = pathlib.Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.RLock()

    def connect(self) -> sqlite3.Connection:
        with self._lock:
            if self._conn is None:
                conn = sqlite3.connect(
                    str(self.db_path), check_same_thread=False, isolation_level=None
                )
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("PRAGMA busy_timeout=5000")
                conn.executescript(_SCHEMA)
                self._conn = conn
            return self._conn

    def execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.connect().execute(sql, params)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                self._conn.close()
                self._conn = None
