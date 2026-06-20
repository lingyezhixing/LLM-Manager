"""Gateway routes. Plan 1 registers only GET /health; Plan 2 adds /v1/models,
OPTIONS preflight, and the non-GET catch-all → proxy.forward."""
from __future__ import annotations

from fastapi import FastAPI


def register_routes(app: FastAPI) -> None:
    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}
