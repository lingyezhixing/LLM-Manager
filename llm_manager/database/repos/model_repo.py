from __future__ import annotations

import time

from sqlalchemy import select

from llm_manager.database.engine import DatabaseEngine
from llm_manager.database.schema import model_runtimes, program_runtimes
from llm_manager.database.repos.base import BaseRepository


class ModelRepository(BaseRepository[dict]):
    def __init__(self, engine: DatabaseEngine):
        super().__init__(engine)

    def record_runtime_start(self, model_name: str, start_time: float) -> int:
        result = self._execute(
            model_runtimes.insert().values(
                model_name=model_name,
                start_time=start_time,
            )
        )
        return result.lastrowid

    def update_runtime_end(self, record_id: int, end_time: float) -> None:
        self._execute(
            model_runtimes.update()
            .where(model_runtimes.c.id == record_id)
            .values(end_time=end_time)
        )

    def get_model_total_runtime(self, model_name: str) -> float:
        rows = self._query(
            select(model_runtimes).where(model_runtimes.c.model_name == model_name)
        )
        total = 0.0
        for row in rows:
            start = row["start_time"]
            end = row.get("end_time") or time.time()
            total += end - start
        return total


class ProgramRepository(BaseRepository[dict]):
    def __init__(self, engine: DatabaseEngine):
        super().__init__(engine)

    def record_start(self, start_time: float) -> int:
        result = self._execute(
            program_runtimes.insert().values(start_time=start_time)
        )
        return result.lastrowid

    def update_end(self, record_id: int, end_time: float) -> None:
        self._execute(
            program_runtimes.update()
            .where(program_runtimes.c.id == record_id)
            .values(end_time=end_time)
        )
