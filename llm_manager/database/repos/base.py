from __future__ import annotations

import time
from typing import Any, Generic, TypeVar

from sqlalchemy import select

from llm_manager.database.engine import DatabaseEngine

T = TypeVar("T")


class BaseRepository(Generic[T]):
    def __init__(self, engine: DatabaseEngine):
        self._engine = engine

    def _execute(self, stmt: Any) -> Any:
        with self._engine.engine.connect() as conn:
            result = conn.execute(stmt)
            conn.commit()
            return result

    def _execute_return_id(self, stmt: Any) -> int:
        with self._engine.engine.connect() as conn:
            result = conn.execute(stmt)
            conn.commit()
            return result.lastrowid

    def _query(self, stmt: Any) -> list[dict]:
        with self._engine.engine.connect() as conn:
            result = conn.execute(stmt)
            return [dict(row._mapping) for row in result]

    def _query_one(self, stmt: Any) -> dict | None:
        rows = self._query(stmt)
        return rows[0] if rows else None

    def _get_or_create_model_id(self, model_name: str) -> int:
        from llm_manager.database.schema import models

        row = self._query_one(
            select(models).where(models.c.original_name == model_name)
        )
        if row:
            return row["id"]
        return self._execute_return_id(
            models.insert().values(
                original_name=model_name, created_at=time.time()
            )
        )
