"""model_requests: record_usage (wall-clock start/end) + usage_series (bucketed by end_time)."""
from llm_manager.data.persistence import open_db, record_usage, usage_series


def test_record_usage_writes_start_end_tokens(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", start=100.0, end=200.0, input_tokens=5, output_tokens=10, cache_n=1, prompt_n=4)
    row = db.conn.execute("SELECT start_time, end_time, input_tokens FROM model_requests").fetchone()
    assert row["start_time"] == 100.0
    assert row["end_time"] == 200.0
    assert row["input_tokens"] == 5


def test_usage_series_buckets_per_model_and_total(tmp_path):
    db = open_db(tmp_path / "t.db")
    # bucket=60, range [0,120) → buckets [0, 60]; the time key is end_time
    record_usage(db, "m1", start=9, end=10, input_tokens=5, output_tokens=5, cache_n=0, prompt_n=5)
    record_usage(db, "m1", start=69, end=70, input_tokens=3, output_tokens=3, cache_n=0, prompt_n=3)
    record_usage(db, "m2", start=19, end=20, input_tokens=2, output_tokens=2, cache_n=0, prompt_n=2)
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


def test_usage_series_buckets_are_clock_aligned_not_start_relative(tmp_path):
    """Buckets align to the clock (multiples of bucket_seconds), independent of the window
    start — so a sliding window scrolls the chart rather than reshuffling each request."""
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", start=69, end=70, input_tokens=1, output_tokens=1, cache_n=0, prompt_n=0)  # end=70 → absolute bucket 60
    result = usage_series(db, start_ts=10, end_ts=130, bucket_seconds=60)  # unaligned start
    assert result.buckets == [0, 60, 120]            # first = floor(10/60)*60 = 0
    assert result.models["m1"] == [0, 2, 0]          # end=70 → bucket 60 → idx 1


def test_migrate_drops_legacy_ts_column(tmp_path):
    """A Round-2 DB with a ts column gets it dropped on open (Option A folds the timestamp
    back into start_time/end_time, now wall-clock as in legacy)."""
    import sqlite3
    p = tmp_path / "legacy.db"
    conn = sqlite3.connect(str(p))
    conn.executescript(
        "CREATE TABLE models (id INTEGER PRIMARY KEY AUTOINCREMENT, original_name TEXT UNIQUE NOT NULL, "
        "created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP);"
        "CREATE TABLE model_requests (id INTEGER PRIMARY KEY AUTOINCREMENT, model_id INTEGER NOT NULL, "
        "ts REAL NOT NULL DEFAULT 0, start_time REAL NOT NULL, end_time REAL NOT NULL, "
        "input_tokens INTEGER NOT NULL, output_tokens INTEGER NOT NULL, cache_n INTEGER NOT NULL, "
        "prompt_n INTEGER NOT NULL, FOREIGN KEY (model_id) REFERENCES models(id));"
        "CREATE INDEX idx_model_requests_ts ON model_requests(ts);"
    )
    conn.commit()
    conn.close()

    db = open_db(p)   # migration drops ts
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(model_requests)")}
    assert "ts" not in cols
    assert "start_time" in cols and "end_time" in cols
