"""GET /api/usage/session — since-start token totals + process start time.

Reads the module-level session aggregate (fed by the proxy's record path). The frontend
refetches the totals every 3s and ticks uptime locally from ``started_at`` (constant), so
the backend never computes a duration. Aggregated data → periodic refetch, not SSE push.
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from llm_manager.data import session


class SessionUsageResponse(BaseModel):
    started_at: float        # process start (wall-clock epoch seconds)
    input_tokens: int
    output_tokens: int
    cache_hit: int
    cache_miss: int
    hit_rate: float


def register_usage_routes(router: APIRouter) -> None:
    @router.get("/usage/session", response_model=SessionUsageResponse)
    def session_usage() -> SessionUsageResponse:
        s = session.snapshot()
        return SessionUsageResponse(
            started_at=s.started_at,
            input_tokens=s.input_tokens,
            output_tokens=s.output_tokens,
            cache_hit=s.cache_hit,
            cache_miss=s.cache_miss,
            hit_rate=s.hit_rate,
        )
