"""GET /api/models (one-shot) + GET /api/models/stream (SSE, event-driven push).

The stream pushes a full snapshot on change (status/pid/pending/failure/last_access),
driven by the subscriber-gated ``ModelFeed``. idle/uptime are intentionally NOT in the
snapshot (they're time-derived): the frontend ticks them locally from ``started_at`` and
``last_access`` (wall-clock epochs). Pydantic schemas → OpenAPI (types hand-mirrored
in frontend/src/lib/api.ts).
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from llm_manager import config, state
from llm_manager.data import logs as _logs
from llm_manager.realtime import ModelFeed


class ModelInfo(BaseModel):
    alias: str                  # cfg.aliases[0] — external identity (same as /v1/models)
    mode: str
    port: int
    auto_start: bool
    status: str                 # state.ModelStatus value
    pid: int | None
    pending: int
    failure_reason: str | None
    started_at: float | None    # wall-clock epoch when entered ROUTING (None if not routing)
    last_access: float          # wall-clock epoch of last activity (0.0 if never)


class ModelsResponse(BaseModel):
    data: list[ModelInfo]


class LogLineResponse(BaseModel):
    id: int
    ts: float
    stream: str
    level: str
    text: str


class LogSearchResponse(BaseModel):
    matches: list[int]   # 匹配行 id(升序)
    total: int


def build_models_response(cfg: config.AppConfig) -> ModelsResponse:
    """Current model snapshot from module-level state + cfg. Shared by GET + ModelFeed.

    No time-derived fields (idle/uptime) — the frontend derives those from the wall-clock
    timestamps so the SSE change-detect only fires on real state changes."""
    items: list[ModelInfo] = []
    for name, m in cfg.models.items():
        items.append(ModelInfo(
            alias=m.aliases[0],
            mode=m.mode,
            port=m.port,
            auto_start=m.auto_start,
            status=state.get_status(name).value,
            pid=state.get_pid(name),
            pending=state.pending_count(name),
            failure_reason=state.get_failure_reason(name),
            started_at=state.get_started_at(name),
            last_access=state.get_last_access_wall(name),
        ))
    return ModelsResponse(data=items)


def _models_event(payload: ModelsResponse) -> str:
    return f"data: {payload.model_dump_json()}\n\n"


async def _models_stream(feed: ModelFeed[ModelsResponse]) -> AsyncIterator[str]:
    """Infinite SSE generator: initial current snapshot, then each change."""
    q = feed.subscribe()
    try:
        yield _models_event(feed.current_snapshot())   # immediate, so the list isn't empty
        while True:
            yield _models_event(await q.get())
    finally:
        feed.unsubscribe(q)


def _to_log_response(line: _logs.LogLine) -> LogLineResponse:
    return LogLineResponse(id=line.id, ts=line.ts, stream=line.stream, level=line.level, text=line.text)


def _log_event(line: _logs.LogLine) -> str:
    return f"data: {_to_log_response(line).model_dump_json()}\n\n"


async def _logs_stream(alias: str, limit: int = 2048, level: str | None = None) -> AsyncIterator[str]:
    """无限 SSE:先回填最近 limit 行(可 level 过滤),再实时推新行。alias 是 primary_name。"""
    q = _logs.subscribe(alias)
    try:
        for line in _logs.backfill(alias, limit, level):
            yield _log_event(line)
        while True:
            yield _log_event(await q.get())
    finally:
        _logs.unsubscribe(alias, q)


def _resolve_alias(alias: str, cfg: config.AppConfig) -> str:
    """URL 收到的是 alias(对外身份),state 以 primary_name(内部键)索引,这里先解析。"""
    try:
        return config.resolve_alias(cfg, alias)
    except KeyError:
        raise HTTPException(404, f"模型别名 '{alias}' 未在配置中找到")


_LOG_LEVELS = ("info", "ok", "warn", "error")


def _level_param(request: Request) -> str | None:
    """?level= 白名单:非 info/ok/warn/error 一律视为不过滤。"""
    lv = request.query_params.get("level")
    return lv if lv in _LOG_LEVELS else None


def register_models_routes(router: APIRouter, cfg: config.AppConfig, lifecycle) -> None:
    @router.get("/models", response_model=ModelsResponse)
    def list_models_status() -> ModelsResponse:
        return build_models_response(cfg)

    @router.get("/models/stream")
    async def stream_models(request: Request) -> StreamingResponse:
        feed: ModelFeed[ModelsResponse] = request.app.state.model_feed
        return StreamingResponse(_models_stream(feed), media_type="text/event-stream")

    @router.post("/models/{alias}/start", status_code=202)
    async def start_model(alias: str) -> Response:
        primary = _resolve_alias(alias, cfg)
        if state.is_runnable(primary):
            raise HTTPException(409, f"model '{alias}' already routing")
        asyncio.create_task(lifecycle.ensure_running(primary))   # fire-and-forget;状态走 /api/models/stream SSE
        return Response(status_code=202)

    @router.post("/models/{alias}/stop", status_code=202)
    async def stop_model(alias: str) -> Response:
        primary = _resolve_alias(alias, cfg)
        asyncio.create_task(lifecycle.stop(primary))             # 运行=停止 / 启动中=中断(协作 stop_event)
        return Response(status_code=202)

    @router.get("/models/{alias}/logs/stream")
    async def stream_logs(alias: str, request: Request) -> StreamingResponse:
        primary = _resolve_alias(alias, cfg)                     # 404 on unknown alias
        # logs 以 primary_name 为键(生命周期 capture 用的就是 primary_name),故传 primary 而非 URL alias
        return StreamingResponse(_logs_stream(primary, level=_level_param(request)),
                                  media_type="text/event-stream")

    @router.get("/models/{alias}/logs/search", response_model=LogSearchResponse)
    def search_logs(alias: str, request: Request) -> LogSearchResponse:
        primary = _resolve_alias(alias, cfg)
        q = request.query_params.get("q", "")
        res = _logs.search(primary, q, _level_param(request))   # 全量检索本次会话日志(可叠加 level)
        return LogSearchResponse(matches=res.matches, total=res.total)

    @router.get("/models/{alias}/logs", response_model=list[LogLineResponse])
    def list_logs(alias: str, request: Request) -> list[LogLineResponse]:
        primary = _resolve_alias(alias, cfg)
        before = request.query_params.get("before")
        limit = max(1, min(5000, int(request.query_params.get("limit", "1500"))))   # 钳制 1..5000
        level = _level_param(request)
        lines = (_logs.before(primary, int(before), limit, level) if before is not None
                 else _logs.backfill(primary, limit, level))
        return [_to_log_response(ll) for ll in lines]
