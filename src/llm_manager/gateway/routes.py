"""Gateway HTTP layer composition root. Wires the management API (/api/*),
catalog (/health, /v1/models, OPTIONS preflight), the OpenAI-compatible proxy
catch-all, and the built-frontend SPA host. See catalog.py, proxy.py, spa.py, api/."""
from __future__ import annotations

from fastapi import FastAPI

from llm_manager import config
from llm_manager.gateway.api import build_api_router
from llm_manager.gateway import catalog, proxy, spa


def register_routes(app: FastAPI, lifecycle, cfg: config.AppConfig, db, client_pool) -> None:
    app.include_router(build_api_router(cfg, lifecycle))              # /api/* 管理 API
    catalog.register_catalog(app, cfg)                                 # /health, /v1/models, OPTIONS 预检
    proxy.register_proxy_routes(app, lifecycle, cfg, db, client_pool)  # OpenAI 代理 catch-all
    spa.register_spa(app)                                              # 前端 SPA(最后注册,GET 兜底不遮蔽前述)
