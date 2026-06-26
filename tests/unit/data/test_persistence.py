"""model_requests wall-clock `ts` column + legacy migration (for the usage time-series)."""
import sqlite3

from llm_manager.data.persistence import open_db, record_usage, usage_series


def test_record_usage_writes_wallclock_ts(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", ts=1000.5, start=1.0, end=2.0,
                 input_tokens=5, output_tokens=10, cache_n=1, prompt_n=4)
    row = db.conn.execute("SELECT ts, input_tokens FROM model_requests").fetchone()
    assert row["ts"] == 1000.5
    assert row["input_tokens"] == 5


def test_open_db_creates_ts_column_and_index(tmp_path):
    db = open_db(tmp_path / "t.db")
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(model_requests)")}
    assert "ts" in cols
    indexes = {r[1] for r in db.conn.execute("PRAGMA index_list(model_requests)")}
    assert any("ts" in i for i in indexes)


def test_open_db_migrates_legacy_model_requests_adds_ts(tmp_path):
    """A DB created before `ts` existed gets the column added on open."""
    p = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(p))
    conn.executescript(
        "CREATE TABLE models (id INTEGER PRIMARY KEY AUTOINCREMENT, original_name TEXT UNIQUE NOT NULL, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE model_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, model_id INTEGER NOT NULL, "
        "start_time REAL NOT NULL, end_time REAL NOT NULL, input_tokens INTEGER NOT NULL, "
        "output_tokens INTEGER NOT NULL, cache_n INTEGER NOT NULL, prompt_n INTEGER NOT NULL, "
        "FOREIGN KEY (model_id) REFERENCES models(id));"
    )
    conn.commit()
    conn.close()

    db = open_db(p)   # migration runs
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(model_requests)")}
    assert "ts" in cols


def test_usage_series_buckets_per_model_and_total(tmp_path):
    db = open_db(tmp_path / "t.db")
    # bucket=60, range [0,120) → buckets [0, 60]
    record_usage(db, "m1", ts=10, start=0, end=1, input_tokens=5, output_tokens=5, cache_n=0, prompt_n=5)
    record_usage(db, "m1", ts=70, start=0, end=1, input_tokens=3, output_tokens=3, cache_n=0, prompt_n=3)
    record_usage(db, "m2", ts=20, start=0, end=1, input_tokens=2, output_tokens=2, cache_n=0, prompt_n=2)
    result = usage_series(db, start_ts=0, end_ts=120, bucket_seconds=60)
    assert result.buckets == [0, 60]
    assert result.models["m1"] == [10, 6]   # 5+5 in bucket 0, 3+3 in bucket 1
    assert result.models["m2"] == [4, 0]    # 2+2 in bucket 0, none → 0-filled
    assert result.total == [14, 6]


def test_usage_series_empty_range_returns_no_buckets(tmp_path):
    db = open_db(tmp_path / "t.db")
    result = usage_series(db, start_ts=0, end_ts=0, bucket_seconds=60)
    assert result.buckets == []
    assert result.total == []
