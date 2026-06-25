"""Gateway routes: GET /health, GET /v1/models (catalog), OPTIONS preflight
short-circuit (204 + open CORS, before body/alias), non-GET catch-all -> proxy.forward."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from llm_manager import config
from llm_manager.gateway import proxy
from llm_manager.gateway.api import build_api_router

_CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "*",
    "access-control-allow-headers": "*",
}


def register_routes(app: FastAPI, lifecycle, cfg: config.AppConfig, db, client_pool) -> None:
    app.include_router(build_api_router(cfg))

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    def list_models() -> dict:
        # id = aliases[0](主别名 = 下游 served name = 客户端调用名);primary_name 仅为内部键,不外露。
        # validate() 保证每个模型至少 1 个别名,故 aliases[0] 恒安全。
        data = [{"id": m.aliases[0], "object": "model"} for m in cfg.models.values()]
        return {"object": "list", "data": data}

    @app.options("/{path:path}")
    def preflight(path: str) -> JSONResponse:
        return JSONResponse(status_code=204, content={}, headers=_CORS)

    # One wrapper per method so each gets a distinct OpenAPI operationId
    # (a single api_route over [POST,PUT,DELETE,PATCH] collides all four onto the
    # same operationId `catch_all__path__post`, producing duplicate keys in the
    # generated schema → breaks OpenAPI consumers incl. our webui codegen).
    async def _forward(path: str, request: Request) -> Response:
        return await proxy.forward(request, path, lifecycle, cfg, db, client_pool)

    @app.post("/{path:path}", operation_id="catch_all__path__post")
    async def catch_all_post(path: str, request: Request) -> Response:
        return await _forward(path, request)

    @app.put("/{path:path}", operation_id="catch_all__path__put")
    async def catch_all_put(path: str, request: Request) -> Response:
        return await _forward(path, request)

    @app.delete("/{path:path}", operation_id="catch_all__path__delete")
    async def catch_all_delete(path: str, request: Request) -> Response:
        return await _forward(path, request)

    @app.patch("/{path:path}", operation_id="catch_all__path__patch")
    async def catch_all_patch(path: str, request: Request) -> Response:
        return await _forward(path, request)
