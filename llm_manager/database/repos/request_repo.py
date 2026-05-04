from __future__ import annotations

from sqlalchemy import select

from llm_manager.database.engine import DatabaseEngine
from llm_manager.database.schema import request_logs
from llm_manager.database.repos.base import BaseRepository


class RequestRepository(BaseRepository[dict]):
    def __init__(self, engine: DatabaseEngine):
        super().__init__(engine)

    def save_request(
        self,
        request_id: str,
        model_name: str,
        timestamp: float,
        prompt_tokens: int,
        completion_tokens: int,
        total_tokens: int,
        latency_ms: float,
        success: bool = True,
        error_message: str | None = None,
    ) -> None:
        self._execute(
            request_logs.insert().values(
                request_id=request_id,
                model_name=model_name,
                timestamp=timestamp,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=total_tokens,
                latency_ms=latency_ms,
                success=1 if success else 0,
                error_message=error_message,
            )
        )

    def get_model_requests(self, model_name: str, limit: int = 100) -> list[dict]:
        return self._query(
            select(request_logs)
            .where(request_logs.c.model_name == model_name)
            .order_by(request_logs.c.timestamp.desc())
            .limit(limit)
        )

    def get_model_token_totals(self, model_name: str) -> dict:
        from sqlalchemy import func

        stmt = select(
            func.sum(request_logs.c.prompt_tokens).label("prompt_tokens"),
            func.sum(request_logs.c.completion_tokens).label("completion_tokens"),
            func.sum(request_logs.c.total_tokens).label("total_tokens"),
            func.count().label("request_count"),
        ).where(request_logs.c.model_name == model_name)

        result = self._query_one(stmt)
        if result and result.get("total_tokens") is not None:
            return result
        return {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "request_count": 0}
