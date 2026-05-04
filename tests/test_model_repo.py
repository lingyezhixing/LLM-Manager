"""Phase 1 — ModelRuntimeRepository 测试"""
import time

from llm_manager.database.repos.model_repo import ModelRuntimeRepository


class TestRecordStart:
    def test_returns_int_id(self, db):
        repo = ModelRuntimeRepository(db)
        rid = repo.record_start("test-model", 1000.0)
        assert isinstance(rid, int)
        assert rid > 0

    def test_creates_model_if_missing(self, db):
        repo = ModelRuntimeRepository(db)
        repo.record_start("new-model", 1000.0)
        mid = repo._get_or_create_model_id("new-model")
        assert mid > 0


class TestRecordEndByName:
    def test_updates_null_end_time(self, db):
        repo = ModelRuntimeRepository(db)
        repo.record_start("test-model", 1000.0)
        repo.record_end_by_name("test-model", 2000.0)
        rows = repo.get_runtime_in_range("test-model", 0, 3000.0)
        assert len(rows) == 1
        assert rows[0]["end_time"] == 2000.0

    def test_no_record_does_nothing(self, db):
        repo = ModelRuntimeRepository(db)
        repo.record_end_by_name("nonexistent", 2000.0)


class TestRecordEndById:
    def test_updates_by_id(self, db):
        repo = ModelRuntimeRepository(db)
        rid = repo.record_start("test-model", 1000.0)
        repo.record_end_by_id(rid, 2000.0)
        rows = repo.get_runtime_in_range("test-model", 0, 3000.0)
        assert len(rows) == 1
        assert rows[0]["end_time"] == 2000.0


class TestGetRuntimeInRange:
    def test_returns_matching_records(self, db):
        repo = ModelRuntimeRepository(db)
        repo.record_start("test-model", 1000.0)
        repo.record_end_by_name("test-model", 1500.0)
        repo.record_start("test-model", 2000.0)
        repo.record_end_by_name("test-model", 2500.0)

        rows = repo.get_runtime_in_range("test-model", 0, 3000.0)
        assert len(rows) == 2

    def test_filters_by_range(self, db):
        repo = ModelRuntimeRepository(db)
        repo.record_start("test-model", 1000.0)
        repo.record_end_by_name("test-model", 1500.0)
        repo.record_start("test-model", 2000.0)
        repo.record_end_by_name("test-model", 2500.0)

        rows = repo.get_runtime_in_range("test-model", 1200.0, 2200.0)
        assert len(rows) == 2

    def test_includes_running_records(self, db):
        repo = ModelRuntimeRepository(db)
        repo.record_start("test-model", 1000.0)

        rows = repo.get_runtime_in_range("test-model", 0, 3000.0)
        assert len(rows) == 1
        assert rows[0]["end_time"] is None

    def test_no_matching_returns_empty(self, db):
        repo = ModelRuntimeRepository(db)
        rows = repo.get_runtime_in_range("nonexistent", 0, 3000.0)
        assert rows == []
