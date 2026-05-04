from __future__ import annotations

from sqlalchemy import and_, select

from llm_manager.database.engine import DatabaseEngine
from llm_manager.database.schema import model_requests, models
from llm_manager.database.repos.base import BaseRepository


class RequestRepository(BaseRepository[dict]):
    def __init__(self, engine: DatabaseEngine):
        super().__init__(engine)

    def save_request(
        self,
        model_name: str,
        start_time: float,
        end_time: float,
        input_tokens: int,
        output_tokens: int,
        cache_n: int,
        prompt_n: int,
    ) -> None:
        model_id = self._get_or_create_model_id(model_name)
        self._execute(
            model_requests.insert().values(
                model_id=model_id,
                start_time=start_time,
                end_time=end_time,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_n=cache_n,
                prompt_n=prompt_n,
            )
        )

    def get_requests(
        self,
        model_name: str,
        start_time: float = 0,
        end_time: float = 0,
        buffer_seconds: int = 60,
    ) -> list[dict]:
        import time

        row = self._query_one(
            select(models).where(models.c.original_name == model_name)
        )
        if not row:
            return []
        model_id = row["id"]

        if end_time == 0:
            end_time = time.time()

        query_start = start_time - buffer_seconds if start_time > 0 else 0

        return self._query(
            select(model_requests)
            .where(
                and_(
                    model_requests.c.model_id == model_id,
                    model_requests.c.end_time >= query_start,
                    model_requests.c.end_time <= end_time,
                )
            )
            .order_by(model_requests.c.end_time.asc())
        )
