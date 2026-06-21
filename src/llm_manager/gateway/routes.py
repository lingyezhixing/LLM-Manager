"""Gateway routes: GET /health, GET /v1/models (catalog), OPTIONS preflight
short-circuit (204 + open CORS, before body/alias), non-GET catch-all -> proxy.forward."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from llm_manager import config
from llm_manager.gateway import proxy

_CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "*",
    "access-control-allow-headers": "*",
}


def register_routes(app: FastAPI, cfg: config.AppConfig) -> None:
    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    def list_models() -> dict:
        data = [{"id": name, "object": "model"} for name in cfg.models]
        return {"object": "list", "data": data}

    @app.options("/{path:path}")
    def preflight(path: str) -> JSONResponse:
        return JSONResponse(status_code=204, content={}, headers=_CORS)

    @app.api_route("/{path:path}", methods=["POST", "PUT", "DELETE", "PATCH"])
    async def catch_all(path: str, request: Request) -> JSONResponse:
        return await proxy.forward(request, path, cfg)
