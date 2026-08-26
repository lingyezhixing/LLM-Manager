"""GET /api/models/stream (SSE,事件驱动推送)+ start/stop/restart。

流在变更时推送完整快照(status/pid/pending/failure/last_access),由订阅者门控的
``ModelFeed`` 驱动。idle/uptime 有意不进快照(它们是时间派生值):前端本地从
``started_at`` 与 ``last_access``(墙上时钟 epoch)自算。Pydantic schema → OpenAPI
(类型手写镜像于 frontend/src/lib/api/models.ts)。
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from llm_manager import bgtask, config, state
from llm_manager.gateway.aliases import resolve_alias_checked
from llm_manager.gateway.api.common import get_config_store, sse_frame
from llm_manager.realtime import ModelFeed


class ModelInfo(BaseModel):
    alias: str  # cfg.aliases[0] — 外部身份(与 /v1/models 相同)
    mode: str
    port: int
    auto_start: bool
    status: str  # state.ModelStatus 值
    pid: int | None
    pending: int
    failure_reason: str | None
    started_at: float | None  # 进入 ROUTING 时的墙上时钟 epoch(未 routing 则为 None)
    last_access: float  # 最近活动的墙上时钟 epoch(从未有活动则为 0.0)


class ModelsResponse(BaseModel):
    data: list[ModelInfo]


def build_models_response(cfg: config.AppConfig) -> ModelsResponse:
    """模块级 state + cfg 的当前模型快照。ModelFeed 快照与 SSE 首帧共用。

    无时间派生字段(idle/uptime)——前端从墙上时钟时间戳自行派生,SSE 变更检测
    只在真实状态变化时触发。"""
    items: list[ModelInfo] = []
    for name, m in cfg.models.items():
        items.append(
            ModelInfo(
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
            )
        )
    return ModelsResponse(data=items)


async def _models_stream(feed: ModelFeed[ModelsResponse]) -> AsyncIterator[str]:
    """无限 SSE 生成器:先发当前快照,之后每次变更各发一帧。"""
    q = feed.subscribe()
    try:
        yield sse_frame(feed.current_snapshot())  # 立即发一帧,列表不至于为空
        while True:
            yield sse_frame(await q.get())
    finally:
        feed.unsubscribe(q)


async def _do_restart(lifecycle, primary: str) -> None:
    """restart = stop → ensure_running。lifecycle 已读穿 → ensure_running 拿最新配置
    (改 port/command/aliases 后 restart 即生效)。"""
    await lifecycle.stop(primary)
    await lifecycle.ensure_running(primary)


def register_models_routes(router: APIRouter, lifecycle) -> None:
    @router.get("/models/stream")
    async def stream_models(request: Request) -> StreamingResponse:
        feed: ModelFeed[ModelsResponse] = request.app.state.model_feed
        return StreamingResponse(_models_stream(feed), media_type="text/event-stream")

    @router.post("/models/{alias}/start", status_code=202)
    async def start_model(alias: str, request: Request) -> Response:
        primary = resolve_alias_checked(get_config_store(request).snapshot(), alias)
        if state.is_runnable(primary):
            raise HTTPException(409, f"model '{alias}' already routing")
        bgtask.run(
            lifecycle.ensure_running(primary)
        )  # fire-and-forget;状态走 /api/models/stream SSE
        return Response(status_code=202)

    @router.post("/models/{alias}/stop", status_code=202)
    async def stop_model(alias: str, request: Request) -> Response:
        primary = resolve_alias_checked(get_config_store(request).snapshot(), alias)
        bgtask.run(lifecycle.stop(primary))  # 运行=停止 / 启动中=中断(协作 stop_event)
        return Response(status_code=202)

    @router.post("/models/{alias}/restart", status_code=202)
    async def restart_model(alias: str, request: Request) -> Response:
        primary = resolve_alias_checked(get_config_store(request).snapshot(), alias)
        bgtask.run(_do_restart(lifecycle, primary))  # 读穿:lifecycle 取新配置;状态走 SSE
        return Response(status_code=202)
