"""Management API (/api/*). One sub-router per resource group."""
from __future__ import annotations

from fastapi import APIRouter

from llm_manager import config
from llm_manager.gateway.api.models import register_models_routes


def build_api_router(cfg: config.AppConfig) -> APIRouter:
    api = APIRouter(prefix="/api")
    register_models_routes(api, cfg)
    return api
