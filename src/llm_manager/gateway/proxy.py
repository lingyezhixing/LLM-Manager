"""Reverse proxy. Phase 0 = 501 stub; Plan 3 fills real forward (ensure_running
+ per-port httpx + header strip + include_usage + SSE branch + token record)."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from llm_manager import config


async def forward(request: Request, path: str, cfg: config.AppConfig) -> JSONResponse:
    return JSONResponse(status_code=501, content={"detail": "proxy not implemented (Plan 3)"})
