"""Gateway routes: GET /health, GET /v1/models (catalog), /api/* management
router (see gateway/api), OPTIONS preflight short-circuit (204 + open CORS,
before body/alias), non-GET catch-all -> proxy.forward, GET catch-all ->
built WebUI SPA (StaticFiles + index.html fallback; see _WEBUI_DIST)."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from llm_manager import config
from llm_manager.gateway import proxy
from llm_manager.gateway.api import build_api_router

# webui 构建产物:src/llm_manager/gateway/routes.py → 仓库根 webui/dist
_WEBUI_DIST = Path(__file__).resolve().parents[3] / "webui" / "dist"

logger = logging.getLogger(__name__)

# 仅预检(OPTIONS)用。真实 GET/POST 响应不带 CORS 头,浏览器仍无法跨源读取;
# 此处只为直连网关的浏览器客户端能预检。显式白名单替代通配(最小权限),
# ACAO 保留 * 因真实响应未启用 CORS 且永不携带凭据。
_CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,POST,PUT,DELETE,PATCH,OPTIONS",
    "access-control-allow-headers": "Content-Type, Authorization",
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

    # WebUI 静态托管(SPA)。仅在已构建时挂载;既有路由(/health,/v1/models,/api/*,代理)
    # 已先注册,不会被覆盖。未命中文件 → 回退 index.html(前端路由)。
    if _WEBUI_DIST.is_dir():
        assets_dir = _WEBUI_DIST / "assets"
        if assets_dir.is_dir():   # dist 存在但缺 assets/ 时不应让整个网关启动崩溃
            app.mount("/assets", StaticFiles(directory=assets_dir), name="webui-assets")

        @app.get("/{path:path}")
        def spa(path: str) -> Response:
            # 不接管 API/代理前缀:未知 /api/*、/v1/* GET 返回 JSON 404,不被 SPA HTML 掩盖
            if path.startswith("api/") or path.startswith("v1/"):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            # 路径必须解析在 dist 内(resolve 折叠 .. 后用 relative_to 校验),防路径穿越
            base = _WEBUI_DIST.resolve()
            candidate = (base / path).resolve()
            try:
                candidate.relative_to(base)
            except ValueError:
                return JSONResponse(status_code=404, content={"detail": "not found"})
            if candidate.is_file():
                return FileResponse(candidate)
            index = base / "index.html"
            if index.is_file():
                return FileResponse(index)
            return JSONResponse(status_code=404, content={"detail": "webui not built"})
    else:
        logger.warning("webui/dist not found at %s; SPA not mounted (run `npm run build` in webui/)", _WEBUI_DIST)
