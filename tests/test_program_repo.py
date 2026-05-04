"""Phase 1 — ProgramRuntimeRepository 测试"""
from llm_manager.database.repos.model_repo import ProgramRuntimeRepository


class TestProgramRuntime:
    def test_record_start_returns_id(self, db):
        repo = ProgramRuntimeRepository(db)
        rid = repo.record_start(1000.0)
        assert isinstance(rid, int)
        assert rid > 0

    def test_update_end(self, db):
        repo = ProgramRuntimeRepository(db)
        rid = repo.record_start(1000.0)
        repo.update_end(rid, 2000.0)
        records = repo.get_runtime_records()
        assert len(records) == 1
        assert records[0]["end_time"] == 2000.0

    def test_get_runtime_records_with_limit(self, db):
        repo = ProgramRuntimeRepository(db)
        for i in range(5):
            rid = repo.record_start(float(1000 + i))
            repo.update_end(rid, float(2000 + i))

        records = repo.get_runtime_records(limit=3)
        assert len(records) == 3

    def test_get_runtime_records_no_limit(self, db):
        repo = ProgramRuntimeRepository(db)
        for i in range(5):
            rid = repo.record_start(float(1000 + i))
            repo.update_end(rid, float(2000 + i))

        records = repo.get_runtime_records()
        assert len(records) == 5
