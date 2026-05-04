from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from llm_manager.api.dependencies import get_service
from llm_manager.services.model_manager import ModelManager

router = APIRouter()


@router.get("")
async def list_models(svc: ModelManager = Depends(get_service(ModelManager))):
    instances = svc.get_all_instances()
    return {
        "models": [
            {
                "name": inst.name,
                "aliases": inst.config.aliases,
                "mode": inst.config.mode,
                "port": inst.config.port,
                "state": inst.state.value,
                "pid": inst.pid,
                "started_at": inst.started_at,
            }
            for inst in instances.values()
        ]
    }


@router.get("/{name}")
async def get_model(name: str, svc: ModelManager = Depends(get_service(ModelManager))):
    resolved = svc.resolve_model_name(name)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"Model '{name}' not found")

    inst = svc.get_instance(resolved)
    return {
        "name": inst.name,
        "aliases": inst.config.aliases,
        "mode": inst.config.mode,
        "port": inst.config.port,
        "state": inst.state.value,
        "pid": inst.pid,
        "started_at": inst.started_at,
        "deployments": {
            k: {
                "required_devices": v.required_devices,
                "script_path": str(v.script_path),
                "memory_mb": v.memory_mb,
            }
            for k, v in inst.config.deployments.items()
        },
    }


@router.post("/{name}/start")
async def start_model(
    name: str,
    deployment: str | None = None,
    svc: ModelManager = Depends(get_service(ModelManager)),
):
    resolved = svc.resolve_model_name(name)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"Model '{name}' not found")

    try:
        inst = await svc.start_model(resolved, deployment)
        return {"status": "ok", "state": inst.state.value}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/{name}/stop")
async def stop_model(name: str, svc: ModelManager = Depends(get_service(ModelManager))):
    resolved = svc.resolve_model_name(name)
    if resolved is None:
        raise HTTPException(status_code=404, detail=f"Model '{name}' not found")

    try:
        inst = await svc.stop_model(resolved)
        return {"status": "ok", "state": inst.state.value}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
