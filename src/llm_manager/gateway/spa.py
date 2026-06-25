"""Built-frontend SPA hosting: StaticFiles(/assets) + GET catch-all fallback to
index.html. Registered LAST (see routes.py) so it never shadows /health,
/v1/models, /api/*, the proxy catch-alls, or FastAPI built-ins."""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

logger = logging.getLogger(__name__)

# 前端构建产物:src/llm_manager/gateway/spa.py → 仓库根 frontend/dist
_FRONTEND_DIST = Path(__file__).resolve().parents[3] / "frontend" / "dist"


def register_spa(app: FastAPI) -> None:
    if not _FRONTEND_DIST.is_dir():
        logger.warning("frontend/dist not found at %s; SPA not mounted (run `npm run build` in frontend/)", _FRONTEND_DIST)
        return

    assets_dir = _FRONTEND_DIST / "assets"
    if assets_dir.is_dir():   # dist 存在但缺 assets/ 时不应让整个网关启动崩溃
        app.mount("/assets", StaticFiles(directory=assets_dir), name="frontend-assets")

    @app.get("/{path:path}")
    def spa(path: str) -> Response:
        # 不接管 API/代理前缀:未知 /api/*、/v1/* GET 返回 JSON 404,不被 SPA HTML 掩盖
        if path.startswith("api/") or path.startswith("v1/"):
            return JSONResponse(status_code=404, content={"detail": "Not Found"})
        # 路径必须解析在 dist 内(resolve 折叠 .. 后用 relative_to 校验),防路径穿越
        base = _FRONTEND_DIST.resolve()
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
        return JSONResponse(status_code=404, content={"detail": "frontend not built"})
