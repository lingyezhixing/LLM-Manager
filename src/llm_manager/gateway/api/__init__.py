"""Management API (/api/*). One sub-router per resource group."""
from __future__ import annotations

from fastapi import APIRouter

from llm_manager import config
from llm_manager.gateway.api.devices import register_devices_routes
from llm_manager.gateway.api.models import register_models_routes
from llm_manager.gateway.api.usage import register_usage_routes


def build_api_router(cfg: config.AppConfig) -> APIRouter:
    api = APIRouter(prefix="/api")
    register_models_routes(api, cfg)
    register_devices_routes(api)
    register_usage_routes(api)
    return api
