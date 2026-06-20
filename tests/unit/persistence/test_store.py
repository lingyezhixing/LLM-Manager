
from llm_manager.persistence.store import SqliteStore


def test_schema_bootstrapped_and_connection_works(tmp_path):
    store = SqliteStore(tmp_path / "t.db")
    store.connect()
    rows = store.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = {r[0] for r in rows}
    assert {"models", "model_requests", "program_runtime", "model_runtime"} <= names
    store.close()


def test_pragmas_set(tmp_path):
    store = SqliteStore(tmp_path / "t.db")
    store.connect()
    journal = store.execute("PRAGMA journal_mode").fetchone()[0]
    fk = store.execute("PRAGMA foreign_keys").fetchone()[0]
    assert journal.lower() == "wal"
    assert fk == 1
    store.close()
