"""GET/POST /api/requests* —— 内存请求簿记(列表 / 详情 / SSE / 取消 / 清空已取消)。

注册表与列表 feed 挂在 ``app.state``(``live_requests`` / ``request_feed``),由
``gateway/routes.py`` 装配。列表帧**不含输入**(每 0.5s 一帧,塞 body 会白白放大流量);
输入只在详情快照里出现一次。
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from llm_manager.gateway.api.common import sse_frame
from llm_manager.gateway.live_requests import (
    TERMINAL_STATUSES,
    RequestInfo,
    RequestRegistry,
)
from llm_manager.realtime import ModelFeed

# 详情快照默认只给尾部 256 KiB(服务端保留全量,不全发给浏览器)。
DEFAULT_TAIL = 262144
MAX_TAIL = 1_048_576


class RequestInfoResponse(BaseModel):
    id: str
    model: str
    alias: str
    method: str
    path: str
    stream: bool
    status: str
    started_at: float
    finished_at: float | None
    request_preview: str
    finish_reason: str | None
    http_status: int | None
    error: str | None
    has_reasoning: bool
    has_tool_calls: bool
    usage: dict | None


class RequestsResponse(BaseModel):
    data: list[RequestInfoResponse]


class RequestDetailResponse(RequestInfoResponse):
    input: dict | None = None
    input_text: str | None = None
    text: str
    seq: int
    truncated: bool


class RequestSnapshotEvent(BaseModel):
    type: Literal["snapshot"] = "snapshot"
    seq: int
    text: str
    truncated: bool
    input: dict | None = None
    input_text: str | None = None
    request: RequestInfoResponse


class RequestDeltaEvent(BaseModel):
    type: Literal["delta"] = "delta"
    seq: int
    text: str


class RequestStateEvent(BaseModel):
    type: Literal["state"] = "state"
    request: RequestInfoResponse


def _to_response(info: RequestInfo) -> RequestInfoResponse:
    return RequestInfoResponse(
        id=info.id,
        model=info.model,
        alias=info.alias,
        method=info.method,
        path=info.path,
        stream=info.stream,
        status=info.status.value,
        started_at=info.started_at,
        finished_at=info.finished_at,
        request_preview=info.request_preview,
        finish_reason=info.finish_reason,
        http_status=info.http_status,
        error=info.error,
        has_reasoning=info.has_reasoning,
        has_tool_calls=info.has_tool_calls,
        usage=info.usage,
    )


def build_requests_response(registry: RequestRegistry) -> RequestsResponse:
    """列表 feed 的快照与 SSE 首帧共用。"""
    return RequestsResponse(data=[_to_response(i) for i in registry.snapshot()])


def _registry(request: Request) -> RequestRegistry:
    return request.app.state.live_requests


async def _feed_stream(feed: ModelFeed[RequestsResponse]) -> AsyncIterator[str]:
    q = feed.subscribe()
    try:
        yield sse_frame(feed.current_snapshot())
        while True:
            yield sse_frame(await q.get())
    finally:
        feed.unsubscribe(q)


async def _detail_stream(live, tail: int) -> AsyncIterator[str]:
    """snapshot → delta* → state(终态后关闭)。"""
    q = live.subscribe()
    try:
        text, truncated = live.tail(tail)
        yield sse_frame(
            RequestSnapshotEvent(
                seq=live.seq,
                text=text,
                truncated=truncated,
                input=live.info.request_body,
                input_text=live.info.input_text,
                request=_to_response(live.info),
            )
        )
        if live.info.status in TERMINAL_STATUSES:
            yield sse_frame(RequestStateEvent(request=_to_response(live.info)))
            return
        while True:
            event = await q.get()
            if event.type == "delta":
                yield sse_frame(RequestDeltaEvent(seq=event.seq, text=event.text))
                continue
            yield sse_frame(RequestStateEvent(request=_to_response(live.info)))
            if live.info.status in TERMINAL_STATUSES:
                return
    finally:
        live.unsubscribe(q)


def register_requests_routes(api: APIRouter) -> None:
    @api.get("/requests", response_model=RequestsResponse)
    def list_requests(
        request: Request,
        model: str | None = None,
        status: str | None = None,
    ) -> RequestsResponse:
        """``model`` 接受别名或 primary(未知值按字面量过滤,不报错——过滤器不该 404)。"""
        items = list(_registry(request).snapshot())
        if model is not None:
            items = [i for i in items if model in (i.model, i.alias)]
        if status is not None:
            items = [i for i in items if i.status.value == status]
        return RequestsResponse(data=[_to_response(i) for i in items])

    @api.get("/requests/stream")
    async def stream_requests(request: Request) -> StreamingResponse:
        feed: ModelFeed[RequestsResponse] = request.app.state.request_feed
        return StreamingResponse(_feed_stream(feed), media_type="text/event-stream")

    @api.post("/requests/cancelled/clear")
    def clear_cancelled(request: Request, model: str | None = None) -> dict:
        return {"cleared": _registry(request).clear_cancelled(model=model)}

    @api.post("/requests/{rid}/cancel", status_code=202)
    async def cancel_request(rid: str, request: Request) -> Response:
        registry = _registry(request)
        live = registry.get(rid)
        if live is None:
            raise HTTPException(404, "请求不存在")
        if live.info.status in TERMINAL_STATUSES or live.finalizing:
            raise HTTPException(409, "请求已结束")
        await registry.cancel(rid)  # CANCELLING 期间返回 False,但对外仍是 202(幂等)
        return Response(status_code=202)

    @api.get("/requests/{rid}", response_model=RequestDetailResponse)
    def request_detail(
        rid: str,
        request: Request,
        tail: int = Query(DEFAULT_TAIL, ge=1, le=MAX_TAIL),
    ) -> RequestDetailResponse:
        live = _registry(request).get(rid)
        if live is None:
            raise HTTPException(404, "请求不存在")
        text, truncated = live.tail(tail)
        return RequestDetailResponse(
            **_to_response(live.info).model_dump(),
            input=live.info.request_body,
            input_text=live.info.input_text,
            text=text,
            seq=live.seq,
            truncated=truncated,
        )

    @api.get("/requests/{rid}/stream")
    async def stream_request_detail(
        rid: str,
        request: Request,
        tail: int = Query(DEFAULT_TAIL, ge=1, le=MAX_TAIL),
    ) -> StreamingResponse:
        live = _registry(request).get(rid)
        if live is None:
            raise HTTPException(404, "请求不存在")
        return StreamingResponse(_detail_stream(live, tail), media_type="text/event-stream")
