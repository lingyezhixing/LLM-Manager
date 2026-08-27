"""GET /api/usage/* — 时间窗口内的 token + 成本聚合。

``session``      = 自启动以来的总计(模块级,由代理喂入),概览卡片每 3s 轮询。
``series``       = 分桶 token 序列(总量 + 按模型),时钟对齐的桶。
``summary``      = 窗口总计(输入/输出/缓存/命中率/请求数)。
``by-model``     = 按模型的窗口统计(token、占比、延迟)。
``cost``         = 窗口成本:tier 模型按请求公式,按时模型按运行重叠。
``cost-series``  = 分桶成本序列(元/桶),与 ``series`` 一样时钟对齐。

前端本地从 ``started_at`` 起算运行时长;series 桶携带墙上时钟 epoch,
图表 x 轴可直接展示。
"""

from __future__ import annotations

import datetime
import logging
import time
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from llm_manager.data.usage import (
    session_snapshot,
    usage_by_model,
    usage_cost,
    usage_cost_series,
    usage_series,
    usage_summary,
)
from llm_manager.gateway.api.common import get_config_store, get_db

logger = logging.getLogger(__name__)


class SessionUsageResponse(BaseModel):
    started_at: float
    input_tokens: int
    output_tokens: int
    cache_hit: int
    cache_miss: int
    hit_rate: float
    total_cost: float = (
        0.0  # 本次启动消耗金额(compute-on-read 窗口 [started_at, now),见模块 docstring)
    )
    local_cost: float = 0.0  # 本地模型部分(by_model.source == "local")
    cloud_cost: float = 0.0  # 云端模型部分(by_model.source == "cloud")


class UsageSeriesResponse(BaseModel):
    buckets: list[float]  # 桶起始的墙上时钟 epoch(图表 x 轴)
    total: list[float]  # 每桶的值,跨模型合计(token 或 元)
    models: dict[str, list[float]]  # 模型名 → 每桶的值


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
    source: str = "local"  # 行来源("local"/"cloud")


class CostByModelResponse(BaseModel):
    model: str
    pricing_type: str
    cost: float
    source: str = "local"  # "local" | "cloud"(计费来源)


class CostSummaryResponse(BaseModel):
    total_cost: float
    by_model: list[CostByModelResponse]
    local_cost: float = 0.0  # by_model 中 source=="local" 的成本和
    cloud_cost: float = 0.0  # by_model 中 source=="cloud" 的成本和


def _bucket_for_span(span: float) -> int:
    """自定义窗口的自动桶大小,按 span 选择(与预设粒度对齐)。"""
    if span <= 3600:
        return 10  # ≤1h → 10s
    if span <= 86_400:
        return 600  # ≤1d → 10min
    if span <= 604_800:
        return 3_600  # ≤7d → 1h
    return 86_400  # → 1 day


def _resolve_range(preset: str, start: float | None, end: float | None) -> tuple[float, float, int]:
    """将预设或自定义 (start, end) 映射为 (start_ts, end_ts, bucket_seconds)。
    桶与本地时钟边界对齐(见 usage_series 的 TZ offset)。"""
    now = time.time()
    if start is not None and end is not None:
        return start, end, _bucket_for_span(end - start)
    if preset == "10m":
        return now - 600, now, 10  # 最近 10 分钟,10s 桶
    if preset == "today":
        midnight = (
            datetime.datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).timestamp()  # noqa: DTZ005 — 本地午夜边界(与 _bucket_axis 的本地 TZ 对齐一致)
        )
        return midnight, now, 600  # 本地午夜起,10min 桶
    if preset == "30d":
        return now - 2_592_000, now, 86_400  # 最近 30 天,1 天桶
    return now - 604_800, now, 3_600  # 默认 + "7d":最近 7 天,1h 桶


def _resolve_window(preset: str, start: float | None, end: float | None) -> tuple[float, float]:
    """非分桶端点的 [start_ts, end_ts) —— 复用 _resolve_range 并丢弃桶大小。"""
    start_ts, end_ts, _ = _resolve_range(preset, start, end)
    return start_ts, end_ts


def register_usage_routes(router: APIRouter) -> None:
    @router.get("/usage/session", response_model=SessionUsageResponse)
    def session_usage_endpoint(request: Request) -> SessionUsageResponse:
        started = getattr(request.app.state, "started_at", None) or time.time()
        s = session_snapshot(started)
        total_cost = 0.0
        local_cost = 0.0
        cloud_cost = 0.0
        store = getattr(request.app.state, "config_store", None)
        if store is not None:
            try:
                # 本次启动消耗 = 窗口 [started_at, now) 的成本(compute-on-read,与用量页同口径;
                # 上一进程的请求/段 end_time < started_at 自然落在窗外)。best-effort:
                # 计费计算失败仅降级为 0,不影响 token 面板。
                cs = usage_cost(
                    get_db(request), store.snapshot(), start_ts=started, end_ts=time.time()
                )
                total_cost = cs.total_cost
                # 三拆:local/cloud 由 by_model 的 source 推导,总账 = local + cloud 恒等
                local_cost = sum(c.cost for c in cs.by_model if c.source == "local")
                cloud_cost = sum(c.cost for c in cs.by_model if c.source == "cloud")
            except Exception:
                logger.warning("session cost computation failed", exc_info=True)
        return SessionUsageResponse(
            started_at=s.started_at,
            input_tokens=s.input_tokens,
            output_tokens=s.output_tokens,
            cache_hit=s.cache_hit,
            cache_miss=s.cache_miss,
            hit_rate=s.hit_rate,
            total_cost=total_cost,
            local_cost=local_cost,
            cloud_cost=cloud_cost,
        )

    @router.get("/usage/series", response_model=UsageSeriesResponse)
    def usage_series_endpoint(
        request: Request,
        period: str = "7d",
        start: float | None = None,
        end: float | None = None,
        source: Literal["all", "local", "cloud"] = "all",
    ) -> UsageSeriesResponse:
        db = get_db(request)
        start_ts, end_ts, bucket = _resolve_range(period, start, end)
        result = usage_series(
            db, start_ts=start_ts, end_ts=end_ts, bucket_seconds=bucket, source=source
        )
        return UsageSeriesResponse(buckets=result.buckets, total=result.total, models=result.models)

    @router.get("/usage/summary", response_model=UsageSummaryResponse)
    def usage_summary_endpoint(
        request: Request,
        period: str = "7d",
        start: float | None = None,
        end: float | None = None,
        source: Literal["all", "local", "cloud"] = "all",
    ) -> UsageSummaryResponse:
        db = get_db(request)
        s_ts, e_ts = _resolve_window(period, start, end)
        s = usage_summary(db, start_ts=s_ts, end_ts=e_ts, source=source)
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
        period: str = "7d",
        start: float | None = None,
        end: float | None = None,
        source: Literal["all", "local", "cloud"] = "all",
    ) -> list[ByModelEntryResponse]:
        db = get_db(request)
        s_ts, e_ts = _resolve_window(period, start, end)
        rows = usage_by_model(db, start_ts=s_ts, end_ts=e_ts, source=source)
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
                source=r.source,
            )
            for r in rows
        ]

    @router.get("/usage/cost", response_model=CostSummaryResponse)
    def usage_cost_endpoint(
        request: Request,
        period: str = "7d",
        start: float | None = None,
        end: float | None = None,
        source: Literal["all", "local", "cloud"] = "all",
    ) -> CostSummaryResponse:
        db = get_db(request)
        cfg = get_config_store(request).snapshot()
        s_ts, e_ts = _resolve_window(period, start, end)
        s = usage_cost(db, cfg, start_ts=s_ts, end_ts=e_ts, source=source)
        return CostSummaryResponse(
            total_cost=s.total_cost,
            by_model=[
                CostByModelResponse(
                    model=r.model, pricing_type=r.pricing_type, cost=r.cost, source=r.source
                )
                for r in s.by_model
            ],
            local_cost=sum(r.cost for r in s.by_model if r.source == "local"),
            cloud_cost=sum(r.cost for r in s.by_model if r.source == "cloud"),
        )

    @router.get("/usage/cost-series", response_model=UsageSeriesResponse)
    def usage_cost_series_endpoint(
        request: Request,
        period: str = "7d",
        start: float | None = None,
        end: float | None = None,
        source: Literal["all", "local", "cloud"] = "all",
    ) -> UsageSeriesResponse:
        db = get_db(request)
        cfg = get_config_store(request).snapshot()
        s_ts, e_ts, bucket = _resolve_range(period, start, end)
        result = usage_cost_series(
            db, cfg, start_ts=s_ts, end_ts=e_ts, bucket_seconds=bucket, source=source
        )
        return UsageSeriesResponse(buckets=result.buckets, total=result.total, models=result.models)
