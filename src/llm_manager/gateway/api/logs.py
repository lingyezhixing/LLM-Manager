"""GET /api/logs/* — persistent session logs (system + model) over SQLite.

Sessions list (with line counts), per-session line paging (backfill/before),
SSE live stream (DB backfill then broadcaster tail), and cross-session text
search. ``model`` query param accepts alias (resolved to primary_name via
config.resolve_alias); logs API reads ``request.app.state.db``.
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from llm_manager import config
from llm_manager.data import logs as _logs
from llm_manager.data import persistence as _p

_LOG_LEVELS = ("info", "ok", "warn", "error")


class LogLineResponse(BaseModel):
    id: int
    ts: float
    stream: str
    level: str
    text: str


class LogSessionResponse(BaseModel):
    id: int
    type: str
    model_name: str | None
    alias: str | None
    start_time: float
    end_time: float | None
    status: str           # "running" | "ended"
    duration_s: float | None
    line_count: int


class LogSearchResponse(BaseModel):
    total: int
    matches: list[dict]   # [{session_id, line: LogLineResponse}]


def _to_line(r) -> LogLineResponse:
    """sqlite3.Row(回填/检索)或 _logs.LogLine(广播实时行) → 统一响应模型。"""
    if isinstance(r, _logs.LogLine):
        return LogLineResponse(id=r.id, ts=r.ts, stream=r.stream,
                               level=r.level, text=r.text)
    return LogLineResponse(id=r["id"], ts=r["ts"], stream=r["stream"],
                           level=r["level"], text=r["text"])


def _to_session(r) -> LogSessionResponse:
    ended = r["end_time"] is not None
    return LogSessionResponse(
        id=r["id"], type=r["type"], model_name=r["model_name"], alias=r["alias"],
        start_time=r["start_time"], end_time=r["end_time"],
        status="ended" if ended else "running",
        duration_s=(r["end_time"] - r["start_time"]) if ended else None,
        line_count=r["line_count"],
    )


def _level_param(request: Request) -> str | None:
    lv = request.query_params.get("level")
    return lv if lv in _LOG_LEVELS else None


def _db(request: Request):
    return request.app.state.db


def _resolve_model(request: Request, model: str | None) -> str | None:
    """model 参数接受 alias → resolve 到 primary_name;未配置 → 404。"""
    if model is None:
        return None
    cfg = request.app.state.config_store.snapshot()
    try:
        return config.resolve_alias(cfg, model)
    except KeyError:
        raise HTTPException(404, f"模型别名 '{model}' 未在配置中找到")


async def _session_stream(session_id: int, level: str | None, db, q) -> AsyncIterator[str]:
    """无限 SSE:先回填最近 limit 行(可 level 过滤),再实时推广播行。

    q 由端点先 subscribe(存在性校验,None → 404——生成器内 raise HTTPException
    不会转成 404,响应头已发)。finally 里 unsubscribe 与端点 subscribe 对称。"""
    try:
        for r in _p.log_lines_backfill(db, session_id, limit=2048, level=level):
            yield f"data: {_to_line(r).model_dump_json()}\n\n"
        while True:
            line = await q.get()
            if level is None or line.level == level:
                yield f"data: {_to_line(line).model_dump_json()}\n\n"
    finally:
        _logs.unsubscribe(session_id, q)


def register_logs_routes(api: APIRouter) -> None:
    @api.get("/logs/sessions", response_model=list[LogSessionResponse])
    def list_sessions(request: Request, type: str | None = None,
                      model: str | None = None, limit: int = 50,
                      before: int | None = None) -> list[LogSessionResponse]:
        t = type if type in ("system", "model") else None
        m = _resolve_model(request, model)
        rows = _p.log_sessions(_db(request), type_=t, model_name=m,
                               limit=limit, before_id=before)
        return [_to_session(r) for r in rows]

    @api.get("/logs/sessions/{session_id}/lines", response_model=list[LogLineResponse])
    def session_lines(session_id: int, request: Request, before: int | None = None,
                      limit: int = 1500) -> list[LogLineResponse]:
        limit = max(1, min(limit, 5000))
        level = _level_param(request)
        if not _p.log_session_exists(_db(request), session_id):
            raise HTTPException(404, "会话不存在")
        rows = (_p.log_lines_before(_db(request), session_id, before, limit, level)
                if before is not None
                else _p.log_lines_backfill(_db(request), session_id, limit, level))
        return [_to_line(r) for r in rows]

    @api.get("/logs/sessions/{session_id}/stream")
    async def stream_session(session_id: int, request: Request) -> StreamingResponse:
        q = _logs.subscribe(session_id)
        if q is None:
            raise HTTPException(404, "会话不存在")
        return StreamingResponse(
            _session_stream(session_id, _level_param(request), _db(request), q),
            media_type="text/event-stream")

    @api.get("/logs/search", response_model=LogSearchResponse)
    def search_logs(request: Request, q: str = "", type: str | None = None,
                    model: str | None = None, session_id: int | None = None,
                    limit: int = 500) -> LogSearchResponse:
        t = type if type in ("system", "model") else None
        m = _resolve_model(request, model)
        level = _level_param(request)
        rows = _p.log_search(_db(request), q, type_=t, model_name=m,
                             session_id=session_id, level=level, limit=limit)
        return LogSearchResponse(
            total=len(rows),
            matches=[{"session_id": r["session_id"], "line": _to_line(r)} for r in rows],
        )
