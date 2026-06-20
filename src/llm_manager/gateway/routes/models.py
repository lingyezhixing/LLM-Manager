from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from llm_manager.config.loader import catalog_domain_models

router = APIRouter()


def _container(request: Request):
    return request.app.state.container


@router.get("/v1/models")
async def list_models(container=Depends(_container)) -> dict:  # noqa: B008
    models = catalog_domain_models(container.config)
    return {
        "object": "list",
        "data": [
            {"id": m.primary_name, "object": "model", "created": 0, "owned_by": "local"}
            for m in models
        ],
    }
