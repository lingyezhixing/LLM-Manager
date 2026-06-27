"""GET /api/models (one-shot) + GET /api/models/stream (SSE, event-driven push).

The stream pushes a full snapshot on change (status/pid/pending/failure/last_access),
driven by the subscriber-gated ``ModelFeed``. idle/uptime are intentionally NOT in the
snapshot (they're time-derived): the frontend ticks them locally from ``started_at`` and
``last_access`` (wall-clock epochs). Pydantic schemas → OpenAPI (types hand-mirrored
in frontend/src/lib/api.ts).
"""
from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from llm_manager import config, state
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


def register_models_routes(router: APIRouter, cfg: config.AppConfig) -> None:
    @router.get("/models", response_model=ModelsResponse)
    def list_models_status() -> ModelsResponse:
        return build_models_response(cfg)

    @router.get("/models/stream")
    async def stream_models(request: Request) -> StreamingResponse:
        feed: ModelFeed[ModelsResponse] = request.app.state.model_feed
        return StreamingResponse(_models_stream(feed), media_type="text/event-stream")
