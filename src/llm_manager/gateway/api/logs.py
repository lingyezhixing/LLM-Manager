"""GET /api/logs/* — persistent session logs (system + model) over SQLite.

Sessions list (with line counts), per-session line paging (backfill/before),
SSE live stream (DB backfill then broadcaster tail), and cross-session text
search. ``model`` query param accepts alias (resolved to primary_name via
config.resolve_alias, falling back to session history for deleted-model
residuals); logs API reads ``get_db(request)``.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

from llm_manager import config
from llm_manager.data import logs as _logs
from llm_manager.gateway.api.common import get_config_store, get_db, sse_frame
from llm_manager.gateway.api.logs_schemas import (
    LogLineResponse,
    LogSearchMatch,
    LogSearchResponse,
    LogSessionResponse,
    _to_line,
    _to_session,
)


def _resolve_model(request: Request, model: str | None) -> str | None:
    """model 参数接受 alias → resolve 到 primary_name;未配置 → 404。

    配置解析失败时回退到会话历史(已删模型的残留会话仍可按其 alias/原名
    过滤查看,见 §8 下拉选项来源);配置与会话历史都无 → 404。"""
    if model is None:
        return None
    cfg = get_config_store(request).snapshot()
    try:
        return config.resolve_alias(cfg, model)
    except KeyError:
        pass
    name = _logs.log_resolve_model_name(get_db(request), model)
    if name is None:
        raise HTTPException(404, f"模型别名 '{model}' 未在配置中找到")
    return name


async def _session_stream(session_id: int, level: str | None, db, q) -> AsyncIterator[str]:
    """无限 SSE:先回填最近 limit 行(可 level 过滤),再实时推广播行。

    q 由端点先 subscribe(存在性校验,None → 404——生成器内 raise HTTPException
    不会转成 404,响应头已发)。finally 里 unsubscribe 与端点 subscribe 对称。"""
    try:
        # 🔵3:回填 2048 行的同步 SQL 移出事件循环线程,避免长订阅首帧阻塞其它请求。
        backfill = await asyncio.to_thread(
            _logs.log_lines_backfill, db, session_id, limit=2048, level=level
        )
        for r in backfill:
            yield sse_frame(_to_line(r))
        while True:
            line = await q.get()
            if level is None or line.level == level:
                yield sse_frame(_to_line(line))
    finally:
        _logs.unsubscribe(session_id, q)


def register_logs_routes(api: APIRouter) -> None:
    @api.get("/logs/sessions", response_model=list[LogSessionResponse])
    def list_sessions(
        request: Request,
        type: Literal["system", "model"] | None = None,
        model: str | None = None,
        limit: int = 50,
        before: int | None = None,
    ) -> list[LogSessionResponse]:
        m = _resolve_model(request, model)
        rows = _logs.log_sessions(
            get_db(request), type_=type, model_name=m, limit=limit, before_id=before
        )
        return [_to_session(r) for r in rows]

    @api.get("/logs/sessions/{session_id}/lines", response_model=list[LogLineResponse])
    def session_lines(
        session_id: int,
        request: Request,
        before: int | None = None,
        limit: int = 1500,
        level: Literal["info", "ok", "warn", "error"] | None = None,
    ) -> list[LogLineResponse]:
        limit = max(1, min(limit, 5000))
        if not _logs.log_session_exists(get_db(request), session_id):
            raise HTTPException(404, "会话不存在")
        rows = (
            _logs.log_lines_before(get_db(request), session_id, before, limit, level)
            if before is not None
            else _logs.log_lines_backfill(get_db(request), session_id, limit, level)
        )
        return [_to_line(r) for r in rows]

    @api.get("/logs/sessions/{session_id}/stream")
    async def stream_session(
        session_id: int,
        request: Request,
        level: Literal["info", "ok", "warn", "error"] | None = None,
    ) -> StreamingResponse:
        q = _logs.subscribe(session_id)
        if q is None:
            raise HTTPException(404, "会话不存在")
        return StreamingResponse(
            _session_stream(session_id, level, get_db(request), q), media_type="text/event-stream"
        )

    @api.get("/logs/search", response_model=LogSearchResponse)
    def search_logs(
        request: Request,
        q: str = "",
        type: Literal["system", "model"] | None = None,
        model: str | None = None,
        session_id: int | None = None,
        level: Literal["info", "ok", "warn", "error"] | None = None,
        limit: int = 500,
    ) -> LogSearchResponse:
        if not q.strip():
            return LogSearchResponse(
                total=0, matches=[]
            )  # 🔵5:空查询无意义,拒空串避免 LIKE '%%' 全表扫描
        m = _resolve_model(request, model)
        total, rows = _logs.log_search(
            get_db(request),
            q,
            type_=type,
            model_name=m,
            session_id=session_id,
            level=level,
            limit=limit,
        )
        return LogSearchResponse(
            total=total,
            matches=[LogSearchMatch(session_id=r["session_id"], line=_to_line(r)) for r in rows],
        )
