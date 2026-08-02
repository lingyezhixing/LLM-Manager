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
from llm_manager.data import persistence as _p
from llm_manager.gateway.api.logs_schemas import LogLineResponse, _level_param, _to_line
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


class ModelLogSearchResponse(BaseModel):
    matches: list[int]   # 匹配行 id(升序)——旧端点契约;新 /api/logs/search 的 LogSearchResponse 形态不同
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


def _log_event(line) -> str:
    return f"data: {_to_line(line).model_dump_json()}\n\n"


def _latest_model_session(db, primary: str) -> int | None:
    """模型最新日志会话 id:运行中 = 当前进行中会话;停止后 = 最近一次(id 最大)→ 历史持久可读。
    从未启动过 → None。"""
    rows = _p.log_sessions(db, type_="model", model_name=primary, limit=1)
    return rows[0]["id"] if rows else None


async def _logs_stream(primary: str, db, limit: int = 2048, level: str | None = None) -> AsyncIterator[str]:
    """无限 SSE:先回填 DB 最近 limit 行(可 level 过滤),再实时推新行。primary 是 primary_name。

    无会话(从未启动)→ 空流;会话已收口(订阅不到)→ 回填后长休眠保持连接打开
    (不发数据也不关闭),避免 EventSource 断线重连反复重放回填行(面板行重复)。"""
    sid = _latest_model_session(db, primary)
    if sid is None:
        return
    for r in _p.log_lines_backfill(db, sid, limit, level):
        yield _log_event(r)
    q = _logs.subscribe(sid)
    try:
        if q is None:
            while True:
                await asyncio.sleep(3600)   # 已收口:长休眠保持连接;aclose/取消可中断
        while True:
            line = await q.get()
            if level is None or line.level == level:
                yield _log_event(line)
    finally:
        if q is not None:
            _logs.unsubscribe(sid, q)


def _resolve_alias(alias: str, cfg: config.AppConfig) -> str:
    """URL 收到的是 alias(对外身份),state 以 primary_name(内部键)索引,这里先解析。"""
    try:
        return config.resolve_alias(cfg, alias)
    except KeyError:
        raise HTTPException(404, f"模型别名 '{alias}' 未在配置中找到")


def _cfg(request: Request) -> config.AppConfig:
    """读穿:每请求从 ConfigStore 取 fresh 快照(P2 模型 CRUD 后不重启即见)。"""
    return request.app.state.config_store.snapshot()


async def _do_restart(lifecycle, primary: str) -> None:
    """restart = stop → ensure_running。lifecycle 已读穿 → ensure_running 拿最新配置
    (改 port/command/aliases 后 restart 即生效)。"""
    await lifecycle.stop(primary)
    await lifecycle.ensure_running(primary)


def register_models_routes(router: APIRouter, lifecycle) -> None:
    @router.get("/models", response_model=ModelsResponse)
    def list_models_status(request: Request) -> ModelsResponse:
        return build_models_response(_cfg(request))

    @router.get("/models/stream")
    async def stream_models(request: Request) -> StreamingResponse:
        feed: ModelFeed[ModelsResponse] = request.app.state.model_feed
        return StreamingResponse(_models_stream(feed), media_type="text/event-stream")

    @router.post("/models/{alias}/start", status_code=202)
    async def start_model(alias: str, request: Request) -> Response:
        primary = _resolve_alias(alias, _cfg(request))
        if state.is_runnable(primary):
            raise HTTPException(409, f"model '{alias}' already routing")
        asyncio.create_task(lifecycle.ensure_running(primary))   # fire-and-forget;状态走 /api/models/stream SSE
        return Response(status_code=202)

    @router.post("/models/{alias}/stop", status_code=202)
    async def stop_model(alias: str, request: Request) -> Response:
        primary = _resolve_alias(alias, _cfg(request))
        asyncio.create_task(lifecycle.stop(primary))             # 运行=停止 / 启动中=中断(协作 stop_event)
        return Response(status_code=202)

    @router.post("/models/{alias}/restart", status_code=202)
    async def restart_model(alias: str, request: Request) -> Response:
        primary = _resolve_alias(alias, _cfg(request))
        asyncio.create_task(_do_restart(lifecycle, primary))     # 读穿:lifecycle 取新配置;状态走 SSE
        return Response(status_code=202)

    @router.get("/models/{alias}/logs/stream")
    async def stream_logs(alias: str, request: Request) -> StreamingResponse:
        primary = _resolve_alias(alias, _cfg(request))           # 404 on unknown alias
        # logs 以 primary_name 为键(生命周期 capture 用的就是 primary_name),故传 primary 而非 URL alias
        return StreamingResponse(_logs_stream(primary, request.app.state.db,
                                              level=_level_param(request)),
                                  media_type="text/event-stream")

    @router.get("/models/{alias}/logs/search", response_model=ModelLogSearchResponse)
    def search_logs(alias: str, request: Request) -> ModelLogSearchResponse:
        primary = _resolve_alias(alias, _cfg(request))
        sid = _latest_model_session(request.app.state.db, primary)
        if sid is None:                                  # 从未启动 → 空
            return ModelLogSearchResponse(matches=[], total=0)
        q = request.query_params.get("q", "")
        # 限定该模型最新会话(历史可搜;可叠加 level);limit=5000 同 list_logs 钳制族——
        # 旧契约返回全部匹配(真 total),不截断 500
        rows = _p.log_search(request.app.state.db, q, type_="model", model_name=primary,
                             session_id=sid, level=_level_param(request), limit=5000)
        return ModelLogSearchResponse(matches=[r["id"] for r in rows], total=len(rows))

    @router.get("/models/{alias}/logs", response_model=list[LogLineResponse])
    def list_logs(alias: str, request: Request) -> list[LogLineResponse]:
        primary = _resolve_alias(alias, _cfg(request))
        before = request.query_params.get("before")
        limit = max(1, min(5000, int(request.query_params.get("limit", "1500"))))   # 钳制 1..5000
        level = _level_param(request)
        sid = _latest_model_session(request.app.state.db, primary)
        if sid is None:                                  # 从未启动 → 空
            return []
        rows = (_p.log_lines_before(request.app.state.db, sid, int(before), limit, level)
                if before is not None
                else _p.log_lines_backfill(request.app.state.db, sid, limit, level))
        return [_to_line(r) for r in rows]
