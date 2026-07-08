"""model_requests persistence: open_db (PRAGMAs + schema), record_usage (wall-clock
start/end), resolve_model_id, lock-serialized concurrency, fetch_usage, usage_series
(bucketed by end_time), and the legacy ``ts`` migration. Consolidated here to mirror the
src layout (src/llm_manager/data/persistence.py)."""
import threading

from llm_manager.data.persistence import (
    fetch_requests,
    fetch_usage,
    open_db,
    record_usage,
    resolve_model_id,
    usage_by_model,
    usage_series,
    usage_summary,
)


def test_open_db_sets_pragmas_and_creates_schema(tmp_path):
    db = open_db(tmp_path / "t.db")
    mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "models" in tables
    assert "model_requests" in tables


def test_record_usage_writes_start_end_tokens(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", start=100.0, end=200.0, input_tokens=5, output_tokens=10, cache_n=1, prompt_n=4)
    row = db.conn.execute("SELECT start_time, end_time, input_tokens FROM model_requests").fetchone()
    assert row["start_time"] == 100.0
    assert row["end_time"] == 200.0
    assert row["input_tokens"] == 5


def test_record_usage_auto_creates_model_and_fetch_round_trips(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "Qwen3-4B", start=1.0, end=2.0, input_tokens=100, output_tokens=50, cache_n=20, prompt_n=80)
    rows = fetch_usage(db, "Qwen3-4B", 0.0, 5.0)
    assert len(rows) == 1
    assert (rows[0]["input_tokens"], rows[0]["output_tokens"]) == (100, 50)


def test_resolve_model_id_is_stable(tmp_path):
    db = open_db(tmp_path / "t.db")
    a = resolve_model_id(db, "M")
    b = resolve_model_id(db, "M")
    assert a == b


def test_concurrent_writes_serialized_by_lock(tmp_path):
    db = open_db(tmp_path / "t.db")
    errors = []

    def write():
        try:
            for _ in range(20):
                record_usage(db, "M", start=0.0, end=0.1, input_tokens=1, output_tokens=1, cache_n=0, prompt_n=1)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=write) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(fetch_usage(db, "M", 0.0, 5.0)) == 80


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


def test_usage_summary_aggregates_half_open_range(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", start=5.0, end=10.0, input_tokens=100, output_tokens=20, cache_n=60, prompt_n=40)
    record_usage(db, "m1", start=15.0, end=20.0, input_tokens=50, output_tokens=10, cache_n=0, prompt_n=50)
    record_usage(db, "m2", start=25.0, end=30.0, input_tokens=10, output_tokens=5, cache_n=10, prompt_n=0)
    # half-open [0, 25): includes end=10,20; excludes end=30
    s = usage_summary(db, start_ts=0.0, end_ts=25.0)
    assert s.request_count == 2
    assert s.input_tokens == 150
    assert s.output_tokens == 30
    assert s.cache_hit == 60
    assert s.cache_miss == 90
    assert s.hit_rate == 60 / 150


def test_usage_summary_empty_range_returns_zeros(tmp_path):
    db = open_db(tmp_path / "t.db")
    s = usage_summary(db, start_ts=0.0, end_ts=10.0)
    assert s.request_count == 0
    assert s.input_tokens == 0
    assert s.cache_hit == 0
    assert s.hit_rate == 0.0


def test_usage_by_model_groups_orders_and_shares(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", start=5.0, end=10.0, input_tokens=100, output_tokens=20, cache_n=60, prompt_n=40)
    record_usage(db, "m2", start=15.0, end=20.0, input_tokens=50, output_tokens=10, cache_n=0, prompt_n=50)
    rows = usage_by_model(db, start_ts=0.0, end_ts=25.0)
    assert [r.model for r in rows] == ["m1", "m2"]   # ordered by input desc
    assert rows[0].input_tokens == 100
    assert rows[0].request_count == 1
    assert rows[0].cache_n == 60
    assert rows[0].share == 100 / 150
    assert rows[1].share == 50 / 150
    assert rows[0].hit_rate == 60 / 100
    assert rows[1].hit_rate == 0.0


def test_usage_by_model_empty_returns_empty_list(tmp_path):
    db = open_db(tmp_path / "t.db")
    assert usage_by_model(db, start_ts=0.0, end_ts=10.0) == []


def test_fetch_requests_orders_newest_first_and_paginates(tmp_path):
    db = open_db(tmp_path / "t.db")
    for i in range(5):
        record_usage(db, "m1", start=float(i), end=float(i + 1),
                     input_tokens=i + 1, output_tokens=0, cache_n=0, prompt_n=0)
    page1 = fetch_requests(db, start_ts=0.0, end_ts=10.0, limit=2)
    assert page1.total == 5
    assert [r.id for r in page1.rows] == [5, 4]    # id DESC = newest first
    assert page1.has_more is True
    assert page1.rows[0].latency_ms == 1000.0       # (end-start)*1000

    page2 = fetch_requests(db, start_ts=0.0, end_ts=10.0, limit=2, before=page1.rows[-1].id)
    assert [r.id for r in page2.rows] == [3, 2]
    assert page2.has_more is True

    page3 = fetch_requests(db, start_ts=0.0, end_ts=10.0, limit=2, before=page2.rows[-1].id)
    assert [r.id for r in page3.rows] == [1]
    assert page3.has_more is False


def test_fetch_requests_filters_by_model(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "m1", start=1.0, end=2.0, input_tokens=1, output_tokens=0, cache_n=0, prompt_n=0)
    record_usage(db, "m2", start=3.0, end=4.0, input_tokens=1, output_tokens=0, cache_n=0, prompt_n=0)
    res = fetch_requests(db, start_ts=0.0, end_ts=10.0, model_name="m1")
    assert res.total == 1
    assert res.rows[0].model == "m1"
