"""Phase 1 — RequestRepository 测试"""
import time

from llm_manager.database.repos.request_repo import RequestRepository


class TestSaveAndGetRequest:
    def test_save_and_get(self, db):
        repo = RequestRepository(db)
        repo.save_request("test-model", 1000.0, 1001.0, 100, 50, 80, 20)
        rows = repo.get_requests("test-model", 0, 2000.0)
        assert len(rows) == 1
        assert rows[0]["input_tokens"] == 100
        assert rows[0]["output_tokens"] == 50

    def test_cache_n_prompt_n_fields(self, db):
        repo = RequestRepository(db)
        repo.save_request("test-model", 1000.0, 1001.0, 100, 50, 80, 20)
        rows = repo.get_requests("test-model", 0, 2000.0)
        assert rows[0]["cache_n"] == 80
        assert rows[0]["prompt_n"] == 20

    def test_multiple_requests(self, db):
        repo = RequestRepository(db)
        repo.save_request("test-model", 1000.0, 1001.0, 10, 5, 0, 10)
        repo.save_request("test-model", 2000.0, 2001.0, 20, 10, 5, 15)
        rows = repo.get_requests("test-model", 0, 3000.0)
        assert len(rows) == 2

    def test_buffer_seconds_expands_range(self, db):
        repo = RequestRepository(db)
        repo.save_request("test-model", 1000.0, 1001.0, 10, 5, 0, 10)
        rows = repo.get_requests("test-model", 1061.0, 2000.0, buffer_seconds=60)
        assert len(rows) == 1

    def test_auto_create_model_on_save(self, db):
        repo = RequestRepository(db)
        repo.save_request("brand-new-model", 1000.0, 1001.0, 10, 5, 0, 10)
        mid = repo._get_or_create_model_id("brand-new-model")
        assert mid > 0

    def test_no_matching_returns_empty(self, db):
        repo = RequestRepository(db)
        rows = repo.get_requests("nonexistent", 0, 3000.0)
        assert rows == []
