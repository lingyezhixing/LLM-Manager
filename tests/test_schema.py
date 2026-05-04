"""Phase 1 — Schema 创建验证"""
from sqlalchemy import inspect

from llm_manager.database.schema import metadata


EXPECTED_TABLES = {
    "models",
    "program_runtimes",
    "model_runtime",
    "model_requests",
    "billing_methods",
    "hourly_pricing",
    "tier_pricing",
}


class TestSchemaCreation:
    def test_all_7_tables_created(self, db):
        inspector = inspect(db.engine)
        tables = set(inspector.get_table_names())
        assert EXPECTED_TABLES.issubset(tables), f"Missing tables: {EXPECTED_TABLES - tables}"

    def test_models_table_columns(self, db):
        inspector = inspect(db.engine)
        columns = {col["name"] for col in inspector.get_columns("models")}
        assert columns == {"id", "original_name", "created_at"}

    def test_model_runtime_end_time_nullable(self, db):
        inspector = inspect(db.engine)
        columns = {col["name"]: col for col in inspector.get_columns("model_runtime")}
        assert columns["end_time"]["nullable"] is True

    def test_model_requests_columns(self, db):
        inspector = inspect(db.engine)
        columns = {col["name"] for col in inspector.get_columns("model_requests")}
        expected = {"id", "model_id", "start_time", "end_time",
                    "input_tokens", "output_tokens", "cache_n", "prompt_n"}
        assert expected.issubset(columns)

    def test_tier_pricing_columns(self, db):
        inspector = inspect(db.engine)
        columns = {col["name"] for col in inspector.get_columns("tier_pricing")}
        expected = {
            "id", "model_id", "tier_index",
            "min_input_tokens", "max_input_tokens",
            "min_output_tokens", "max_output_tokens",
            "input_price", "output_price",
            "support_cache", "cache_write_price", "cache_read_price",
        }
        assert expected.issubset(columns)
