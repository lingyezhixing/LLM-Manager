"""Phase 1 — BaseRepository 测试"""
from llm_manager.database.repos.base import BaseRepository


class TestGetOrCreateModelId:
    def test_new_model_returns_id(self, db):
        repo = BaseRepository(db)
        mid = repo._get_or_create_model_id("test-model")
        assert isinstance(mid, int)
        assert mid > 0

    def test_same_name_returns_same_id(self, db):
        repo = BaseRepository(db)
        id1 = repo._get_or_create_model_id("model-a")
        id2 = repo._get_or_create_model_id("model-a")
        assert id1 == id2

    def test_different_names_return_different_ids(self, db):
        repo = BaseRepository(db)
        id1 = repo._get_or_create_model_id("model-a")
        id2 = repo._get_or_create_model_id("model-b")
        assert id1 != id2

    def test_rapid_calls_no_error(self, db):
        repo = BaseRepository(db)
        ids = [repo._get_or_create_model_id("model-x") for _ in range(10)]
        assert len(set(ids)) == 1
