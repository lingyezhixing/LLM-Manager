"""Catalog + preflight routes: GET /health, GET /v1/models (id=aliases[0]),
OPTIONS preflight short-circuit (204 + CORS, before body/alias)."""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from llm_manager import config

# 仅预检(OPTIONS)用。真实 GET/POST 响应不带 CORS 头,浏览器仍无法跨源读取;
# 此处只为直连网关的浏览器客户端能预检。显式白名单替代通配(最小权限),
# ACAO 保留 * 因真实响应未启用 CORS 且永不携带凭据。
_CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,POST,PUT,DELETE,PATCH,OPTIONS",
    "access-control-allow-headers": "Content-Type, Authorization",
}


def register_catalog(app: FastAPI, cfg: config.AppConfig) -> None:
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
