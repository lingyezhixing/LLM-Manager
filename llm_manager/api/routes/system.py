from __future__ import annotations

import platform
import sys

from fastapi import APIRouter, Depends

from llm_manager.api.dependencies import get_service
from llm_manager.services.model_manager import ModelManager

router = APIRouter()


@router.get("/info")
async def system_info(svc: ModelManager = Depends(get_service(ModelManager))):
    instances = svc.get_all_instances()
    running = sum(1 for i in instances.values() if i.state.value == "running")
    return {
        "python": sys.version,
        "platform": platform.platform(),
        "models_total": len(instances),
        "models_running": running,
    }


@router.get("/health")
async def health_check():
    return {"status": "ok"}
