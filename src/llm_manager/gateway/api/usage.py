"""GET /api/usage/session — since-start token totals for the 概览 session-stats card.

Reads the module-level session aggregate (fed by the proxy's record path). The frontend
refetches this every 3s (aggregated data → periodic refetch, not SSE push).
"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel

from llm_manager.data import session


class SessionUsageResponse(BaseModel):
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
            input_tokens=s.input_tokens,
            output_tokens=s.output_tokens,
            cache_hit=s.cache_hit,
            cache_miss=s.cache_miss,
            hit_rate=s.hit_rate,
        )
