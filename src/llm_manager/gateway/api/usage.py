"""GET /api/usage/session (since-start totals) + GET /api/usage/series (token time-series).

``session`` = module-level aggregate (proxy-fed), refetched every 3s by the 概览 card.
``series``  = bucketed per-model + total token consumption, refetched per-preset cadence.
The frontend ticks uptime locally from ``started_at``; series buckets carry wall-clock
epochs so the chart's x-axis is displayable.
"""
from __future__ import annotations

import datetime
import time

from fastapi import APIRouter, Request
from pydantic import BaseModel

from llm_manager.data import session
from llm_manager.data.persistence import usage_by_model, usage_series, usage_summary


class SessionUsageResponse(BaseModel):
    started_at: float
    input_tokens: int
    output_tokens: int
    cache_hit: int
    cache_miss: int
    hit_rate: float


class UsageSeriesResponse(BaseModel):
    buckets: list[float]            # bucket-start wall-clock epochs (chart x-axis)
    total: list[float]              # value per bucket, summed across models (tokens 或 元)
    models: dict[str, list[float]]  # model name → value per bucket


class UsageSummaryResponse(BaseModel):
    input_tokens: int
    output_tokens: int
    cache_hit: int
    cache_miss: int
    hit_rate: float
    request_count: int


class ByModelEntryResponse(BaseModel):
    model: str
    input_tokens: int
    output_tokens: int
    cache_n: int
    request_count: int
    hit_rate: float
    share: float
    latency_ms: float


def _bucket_for_span(span: float) -> int:
    """Auto bucket size for a custom range, chosen by span (matches preset granularities)."""
    if span <= 3600:
        return 10           # ≤1h → 10s
    if span <= 86_400:
        return 600          # ≤1d → 10min
    if span <= 604_800:
        return 3_600        # ≤7d → 1h
    return 86_400           # → 1 day


def _resolve_range(preset: str, start: float | None, end: float | None) -> tuple[float, float, int]:
    """Map a preset or custom (start, end) to (start_ts, end_ts, bucket_seconds).
    Buckets align to local clock boundaries (see usage_series TZ offset)."""
    now = time.time()
    if start is not None and end is not None:
        return start, end, _bucket_for_span(end - start)
    if preset == "10m":
        return now - 600, now, 10                  # last 10 min, 10s buckets
    if preset == "today":
        midnight = datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
        return midnight, now, 600                  # since local midnight, 10min buckets
    if preset == "30d":
        return now - 2_592_000, now, 86_400        # last 30 days, 1-day buckets
    return now - 604_800, now, 3_600               # default + "7d": last 7 days, 1h buckets


def _resolve_window(preset: str, start: float | None, end: float | None) -> tuple[float, float]:
    """[start_ts, end_ts) for the non-bucketed endpoints — reuses _resolve_range and
    discards the bucket size."""
    start_ts, end_ts, _ = _resolve_range(preset, start, end)
    return start_ts, end_ts


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

    @router.get("/usage/series", response_model=UsageSeriesResponse)
    def usage_series_endpoint(
        request: Request,
        range: str = "7d",
        start: float | None = None,
        end: float | None = None,
    ) -> UsageSeriesResponse:
        db = request.app.state.db
        start_ts, end_ts, bucket = _resolve_range(range, start, end)
        result = usage_series(db, start_ts=start_ts, end_ts=end_ts, bucket_seconds=bucket)
        return UsageSeriesResponse(buckets=result.buckets, total=result.total, models=result.models)

    @router.get("/usage/summary", response_model=UsageSummaryResponse)
    def usage_summary_endpoint(
        request: Request,
        range: str = "7d",
        start: float | None = None,
        end: float | None = None,
    ) -> UsageSummaryResponse:
        db = request.app.state.db
        s_ts, e_ts = _resolve_window(range, start, end)
        s = usage_summary(db, start_ts=s_ts, end_ts=e_ts)
        return UsageSummaryResponse(
            input_tokens=s.input_tokens,
            output_tokens=s.output_tokens,
            cache_hit=s.cache_hit,
            cache_miss=s.cache_miss,
            hit_rate=s.hit_rate,
            request_count=s.request_count,
        )

    @router.get("/usage/by-model", response_model=list[ByModelEntryResponse])
    def usage_by_model_endpoint(
        request: Request,
        range: str = "7d",
        start: float | None = None,
        end: float | None = None,
    ) -> list[ByModelEntryResponse]:
        db = request.app.state.db
        s_ts, e_ts = _resolve_window(range, start, end)
        rows = usage_by_model(db, start_ts=s_ts, end_ts=e_ts)
        return [
            ByModelEntryResponse(
                model=r.model,
                input_tokens=r.input_tokens,
                output_tokens=r.output_tokens,
                cache_n=r.cache_n,
                request_count=r.request_count,
                hit_rate=r.hit_rate,
                share=r.share,
                latency_ms=r.latency_ms,
            )
            for r in rows
        ]
