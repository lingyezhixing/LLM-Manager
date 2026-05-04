from __future__ import annotations

from sqlalchemy import and_, or_, select, update

from llm_manager.database.engine import DatabaseEngine
from llm_manager.database.schema import model_runtime, models, program_runtimes
from llm_manager.database.repos.base import BaseRepository


class ModelRuntimeRepository(BaseRepository[dict]):
    def __init__(self, engine: DatabaseEngine):
        super().__init__(engine)

    def record_start(self, model_name: str, start_time: float) -> int:
        model_id = self._get_or_create_model_id(model_name)
        return self._execute_return_id(
            model_runtime.insert().values(
                model_id=model_id, start_time=start_time
            )
        )

    def record_end_by_name(self, model_name: str, end_time: float) -> None:
        row = self._query_one(
            select(models).where(models.c.original_name == model_name)
        )
        if not row:
            return
        model_id = row["id"]
        latest = self._query_one(
            select(model_runtime)
            .where(
                and_(
                    model_runtime.c.model_id == model_id,
                    model_runtime.c.end_time.is_(None),
                )
            )
            .order_by(model_runtime.c.id.desc())
            .limit(1)
        )
        if latest:
            self._execute(
                update(model_runtime)
                .where(model_runtime.c.id == latest["id"])
                .values(end_time=end_time)
            )

    def record_end_by_id(self, record_id: int, end_time: float) -> None:
        self._execute(
            update(model_runtime)
            .where(model_runtime.c.id == record_id)
            .values(end_time=end_time)
        )

    def get_runtime_in_range(
        self, model_name: str, start: float, end: float
    ) -> list[dict]:
        row = self._query_one(
            select(models).where(models.c.original_name == model_name)
        )
        if not row:
            return []
        model_id = row["id"]
        return self._query(
            select(model_runtime)
            .where(
                and_(
                    model_runtime.c.model_id == model_id,
                    model_runtime.c.start_time <= end,
                    or_(
                        model_runtime.c.end_time >= start,
                        model_runtime.c.end_time.is_(None),
                    ),
                )
            )
            .order_by(model_runtime.c.start_time.asc())
        )


class ProgramRuntimeRepository(BaseRepository[dict]):
    def __init__(self, engine: DatabaseEngine):
        super().__init__(engine)

    def record_start(self, start_time: float) -> int:
        return self._execute_return_id(
            program_runtimes.insert().values(start_time=start_time)
        )

    def update_end(self, record_id: int, end_time: float) -> None:
        self._execute(
            program_runtimes.update()
            .where(program_runtimes.c.id == record_id)
            .values(end_time=end_time)
        )

    def get_runtime_records(self, limit: int = 0) -> list[dict]:
        stmt = (
            select(program_runtimes)
            .order_by(program_runtimes.c.id.desc())
        )
        if limit > 0:
            stmt = stmt.limit(limit)
        return self._query(stmt)
