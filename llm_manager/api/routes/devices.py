from __future__ import annotations

from fastapi import APIRouter, Depends

from llm_manager.api.dependencies import get_service
from llm_manager.services.device_monitor import DeviceMonitor

router = APIRouter()


@router.get("")
async def list_devices(svc: DeviceMonitor = Depends(get_service(DeviceMonitor))):
    statuses = svc.get_all_statuses()
    return {
        "devices": [
            {
                "name": s.name,
                "state": s.state.value,
                "memory_total_mb": s.memory_total_mb,
                "memory_used_mb": s.memory_used_mb,
                "memory_free_mb": s.memory_free_mb,
                "temperature": s.temperature,
                "utilization": s.utilization,
            }
            for s in statuses.values()
        ]
    }


@router.get("/{device_name}")
async def get_device(
    device_name: str,
    svc: DeviceMonitor = Depends(get_service(DeviceMonitor)),
):
    status = svc.get_status(device_name)
    if status is None:
        return {"error": f"Device '{device_name}' not found"}
    return {
        "name": status.name,
        "state": status.state.value,
        "memory_total_mb": status.memory_total_mb,
        "memory_used_mb": status.memory_used_mb,
        "memory_free_mb": status.memory_free_mb,
        "temperature": status.temperature,
        "utilization": status.utilization,
    }
