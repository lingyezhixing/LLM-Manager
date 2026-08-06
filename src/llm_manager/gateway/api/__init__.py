"""Management API (/api/*). One sub-router per resource group."""

from __future__ import annotations

from fastapi import APIRouter

from llm_manager.gateway.api.config_api import register_config_routes
from llm_manager.gateway.api.data_api import register_data_routes
from llm_manager.gateway.api.devices import register_devices_routes
from llm_manager.gateway.api.logs import register_logs_routes
from llm_manager.gateway.api.models import register_models_routes
from llm_manager.gateway.api.usage import register_usage_routes


def build_api_router(lifecycle) -> APIRouter:
    api = APIRouter(prefix="/api")
    register_models_routes(api, lifecycle)
    register_devices_routes(api)
    register_usage_routes(api)
    register_logs_routes(api)
    register_config_routes(api)
    register_data_routes(api)
    return api
