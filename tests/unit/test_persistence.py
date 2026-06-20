import threading

from llm_manager.data.persistence import open_db, record_usage, fetch_usage, resolve_model_id


def test_open_db_sets_pragmas_and_creates_schema(tmp_path):
    db = open_db(tmp_path / "t.db")
    mode = db.conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert mode.lower() == "wal"
    tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert "models" in tables
    assert "model_requests" in tables


def test_record_usage_auto_creates_model_and_round_trips(tmp_path):
    db = open_db(tmp_path / "t.db")
    record_usage(db, "Qwen3-4B", 1.0, 2.0, 100, 50, 20, 80)
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
                record_usage(db, "M", 0.0, 0.1, 1, 1, 0, 1)
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=write) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert len(fetch_usage(db, "M", 0.0, 5.0)) == 80
