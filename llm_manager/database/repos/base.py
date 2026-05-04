from __future__ import annotations

from typing import Any, Generic, TypeVar

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

    def _query(self, stmt: Any) -> list[dict]:
        with self._engine.engine.connect() as conn:
            result = conn.execute(stmt)
            return [dict(row._mapping) for row in result]

    def _query_one(self, stmt: Any) -> dict | None:
        rows = self._query(stmt)
        return rows[0] if rows else None
