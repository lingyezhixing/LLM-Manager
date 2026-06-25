"""GET /api/models — model list with live lifecycle status + config metadata.

Reads module-level state (single-thread event loop → no locks) + cfg. Serves as
the contract surface for the WebUI; Pydantic response_model → named OpenAPI
schemas → clean generated TS types (see frontend codegen)."""
from __future__ import annotations

import time

from fastapi import APIRouter
from pydantic import BaseModel

from llm_manager import config, state


class ModelInfo(BaseModel):
    alias: str                 # cfg.aliases[0] — external identity (same as /v1/models)
    mode: str
    port: int
    auto_start: bool
    status: str                # state.ModelStatus value
    pid: int | None
    pending: int
    idle_seconds: float | None  # monotonic now - last_access; None if never accessed
    failure_reason: str | None


class ModelsResponse(BaseModel):
    data: list[ModelInfo]


def register_models_routes(router: APIRouter, cfg: config.AppConfig) -> None:
    @router.get("/models", response_model=ModelsResponse)
    def list_models_status() -> ModelsResponse:
        now = time.monotonic()
        items: list[ModelInfo] = []
        for name, m in cfg.models.items():
            last = state.get_last_access(name)
            idle = None if last == 0.0 else round(now - last, 1)
            items.append(ModelInfo(
                alias=m.aliases[0],
                mode=m.mode,
                port=m.port,
                auto_start=m.auto_start,
                status=state.get_status(name).value,
                pid=state.get_pid(name),
                pending=state.pending_count(name),
                idle_seconds=idle,
                failure_reason=state.get_failure_reason(name),
            ))
        return ModelsResponse(data=items)
