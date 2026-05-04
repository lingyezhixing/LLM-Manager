from __future__ import annotations

from fastapi import FastAPI

from llm_manager.api.middleware.error_handler import ErrorHandlerMiddleware
from llm_manager.api.middleware.logging import RequestLoggingMiddleware
from llm_manager.api.routes import analytics, billing, devices, models, proxy, system
from llm_manager.container import Container


def create_api_app(container: Container) -> FastAPI:
    app = FastAPI(title="LLM-Manager API", version="2.0.0")
    app.state.container = container

    app.add_middleware(ErrorHandlerMiddleware)
    app.add_middleware(RequestLoggingMiddleware)

    app.include_router(models.router, prefix="/api/models")
    app.include_router(proxy.router, prefix="/api/proxy/v1")
    app.include_router(proxy.router, prefix="/v1")
    app.include_router(devices.router, prefix="/api/devices")
    app.include_router(billing.router, prefix="/api/billing")
    app.include_router(analytics.router, prefix="/api/analytics")
    app.include_router(system.router, prefix="/api/system")

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    return app
