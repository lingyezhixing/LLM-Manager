from llm_manager.domain.meter import TokenUsage
from llm_manager.domain.records import RequestRecord
from llm_manager.persistence.repository import Repository
from llm_manager.persistence.store import SqliteStore


def _repo(tmp_path):
    store = SqliteStore(tmp_path / "t.db")
    store.connect()
    return Repository(store)


def test_record_usage_inserts_row(tmp_path):
    repo = _repo(tmp_path)
    repo.record_usage(RequestRecord("qwen", 1.0, 2.0, TokenUsage(10, 4, 0, 10)))
    rows = repo.store.execute(
        "SELECT model_id, input_tokens, output_tokens, cache_n, prompt_n FROM model_requests"
    ).fetchall()
    assert len(rows) == 1
    got = (
        rows[0]["input_tokens"],
        rows[0]["output_tokens"],
        rows[0]["cache_n"],
        rows[0]["prompt_n"],
    )
    assert got == (10, 4, 0, 10)


def test_record_usage_creates_model_identity(tmp_path):
    repo = _repo(tmp_path)
    repo.record_usage(RequestRecord("qwen", 1.0, 2.0, TokenUsage(1, 1, 0, 1)))
    repo.record_usage(RequestRecord("qwen", 3.0, 4.0, TokenUsage(1, 1, 0, 1)))  # same model reused
    names = {r[0] for r in repo.store.execute("SELECT original_name FROM models").fetchall()}
    assert names == {"qwen"}


def test_record_usage_skips_all_zero(tmp_path):
    repo = _repo(tmp_path)
    repo.record_usage(RequestRecord("qwen", 1.0, 2.0, TokenUsage(0, 0, 0, 0)))
    rows = repo.store.execute("SELECT COUNT(*) FROM model_requests").fetchone()
    assert rows[0] == 0


def test_program_and_model_sessions(tmp_path):
    repo = _repo(tmp_path)
    repo.record_program_start(100.0)
    repo.record_program_end(110.0)
    repo.record_model_start("qwen", 100.0)
    repo.record_model_end("qwen", 105.0)
    prog = repo.store.execute("SELECT start_time, end_time FROM program_runtime").fetchone()
    assert (prog["start_time"], prog["end_time"]) == (100.0, 110.0)
    mr = repo.store.execute("SELECT start_time, end_time FROM model_runtime").fetchone()
    assert (mr["start_time"], mr["end_time"]) == (100.0, 105.0)
