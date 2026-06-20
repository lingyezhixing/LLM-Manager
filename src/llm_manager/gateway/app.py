"""create_app(container) -> FastAPI. CORS middleware, ApiError handler, /health +
/v1/models routers, and the non-GET catch-all proxy (-> GatewayPort.forward).
No SPA/static routes, no dashboard routes (spec §12)."""

from __future__ import annotations

from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from llm_manager.bootstrap.lifespan import make_lifespan
from llm_manager.gateway.errors import ApiError, api_error_handler
from llm_manager.gateway.routes.health import router as health_router
from llm_manager.gateway.routes.models import router as models_router

_PROXY_METHODS = ["POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"]


def _container(request: Request):
    return request.app.state.container


def create_app(container) -> FastAPI:
    app = FastAPI(title="LLM-Manager API", lifespan=make_lifespan(container))
    app.state.container = container

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(ApiError, api_error_handler)

    app.include_router(health_router)
    app.include_router(models_router)

    @app.api_route("/{path:path}", methods=_PROXY_METHODS)
    async def proxy_catchall(request: Request, container=Depends(_container)):  # noqa: B008
        from llm_manager.ports.gateway import ProxyRequest

        body = await request.body()
        preq = ProxyRequest(
            method=request.method,
            path=request.url.path,
            headers=dict(request.headers),
            body=body,
            query_params=dict(request.query_params),
        )
        presp = await container.gateway.forward(preq)
        return JSONResponse(
            status_code=presp.status_code,
            content=(
                {"error": {"type": "not_implemented", "message": "proxy not implemented"}}
                if presp.status_code >= 400
                else {}
            ),
            headers=presp.headers,
        )

    return app
