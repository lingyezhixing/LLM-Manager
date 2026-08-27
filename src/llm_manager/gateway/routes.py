"""Gateway HTTP 层组合根:装配管理 API(/api/*)、catalog(/health、/v1/models、
OPTIONS 预检)、OpenAI 兼容代理 catch-all 与前端构建产物 SPA 宿主。见 proxy.py、api/。"""

from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from llm_manager.gateway.api import build_api_router
from llm_manager.gateway.proxy import register_proxy_routes

logger = logging.getLogger(__name__)

# 前端构建产物:仓库根 frontend/dist
_FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"

# 仅预检(OPTIONS)用。真实 GET/POST 响应不带 CORS 头,浏览器仍无法跨源读取;
# 此处只为直连网关的浏览器客户端能预检。显式白名单替代通配(最小权限),
# ACAO 保留 * 因真实响应未启用 CORS 且永不携带凭据。
_CORS = {
    "access-control-allow-origin": "*",
    "access-control-allow-methods": "GET,POST,PUT,DELETE,PATCH,OPTIONS",
    "access-control-allow-headers": "Content-Type, Authorization",
}


def register_routes(app: FastAPI, lifecycle, db, client_pool) -> None:
    app.include_router(build_api_router(lifecycle))  # /api/* 管理 API
    _register_catalog(app)  # /health, /v1/models, OPTIONS 预检
    register_proxy_routes(app, lifecycle, db, client_pool)  # OpenAI 代理 catch-all
    _register_spa(app)  # 前端 SPA(最后注册,GET 兜底不遮蔽前述)


def _register_catalog(app: FastAPI) -> None:
    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/models")
    def list_models(request: Request) -> dict:
        # id = aliases[0](主别名 = 下游 served name = 客户端调用名);模型名仅为内部键,不外露。
        # validate() 保证每个模型至少 1 个别名,故 aliases[0] 恒安全。读穿:每请求取 fresh 快照。
        # 云端合并:启用服务商的每个云模型以 {provider}/{model} 对外暴露,禁用服务商不暴露。
        cfg = request.app.state.config_store.snapshot()
        data = [{"id": m.aliases[0], "object": "model"} for m in cfg.models.values()]
        for p in cfg.cloud_providers.values():
            if not p.enabled:
                continue
            for cm in p.models:
                data.append({"id": f"{p.name}/{cm.model_name}", "object": "model"})
        return {"object": "list", "data": data}

    @app.options("/{path:path}")
    def preflight(path: str) -> JSONResponse:
        return JSONResponse(status_code=204, content={}, headers=_CORS)


def _media_type(path: str) -> str | None:
    """Service-spawned file MIME。Windows 上 mimetypes 把 .svg 判为非注册类型
    image/svg,标准 Chromium 对 favicon 会拒绝解码(favicon 不入库)——恒覆写为
    image/svg+xml 消除平台分歧(favicon 因错误 MIME 无法入库为实测结论)。"""
    media_type = mimetypes.guess_type(path)[0]
    if path.endswith(".svg"):
        return "image/svg+xml"
    return media_type


def _register_spa(app: FastAPI) -> None:
    """前端构建产物 SPA 托管:StaticFiles(/assets) + GET catch-all 回退到 index.html。
    最后注册,绝不遮蔽 /health、/v1/models、/api/*、代理 catch-all 或 FastAPI 内建路由。"""
    if not _FRONTEND_DIST.is_dir():
        logger.warning(
            "frontend/dist not found at %s; SPA not mounted (run `npm run build` in frontend/)",
            _FRONTEND_DIST,
        )
        return

    assets_dir = _FRONTEND_DIST / "assets"
    if assets_dir.is_dir():  # dist 存在但缺 assets/ 时不应让整个网关启动崩溃
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/{path:path}")
    def spa(path: str) -> Response:
        # 不接管 API/代理前缀:未知 /api/*、/v1/* GET 返回 JSON 404,不被 SPA HTML 掩盖
        if path.startswith(("api/", "v1/")):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        # 路径必须解析在 dist 内(resolve 折叠 .. 后用 relative_to 校验),防路径穿越
        base = _FRONTEND_DIST.resolve()
        candidate = (base / path).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            return JSONResponse(status_code=404, content={"detail": "not found"})
        if candidate.is_file():
            return FileResponse(candidate, media_type=_media_type(candidate.name))
        index = base / "index.html"
        if index.is_file():
            return FileResponse(index)
        return JSONResponse(status_code=404, content={"detail": "frontend not built"})
