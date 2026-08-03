"""GET /api/devices/stream (SSE live push) for the device bar.

On connect it sends the current snapshot immediately, then each refresh from the
subscriber-gated ``DeviceFeed`` (2s). Pydantic schemas → OpenAPI (types hand-mirrored
in ``frontend/src/lib/api.ts``). The SSE generator is extracted (``_device_stream``)
so it can be unit-tested directly without the HTTP stack.
"""
from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import asdict

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from llm_manager.devices import DeviceInfo
from llm_manager.realtime import DeviceFeed


class DeviceInfoResponse(BaseModel):
    device_name: str
    device_type: str
    memory_type: str
    total_memory_mb: int
    available_memory_mb: int
    used_memory_mb: int
    usage_percentage: float
    temperature_celsius: float | None


class DevicesResponse(BaseModel):
    data: list[DeviceInfoResponse]


def _to_schema(d: DeviceInfo) -> DeviceInfoResponse:
    return DeviceInfoResponse(**asdict(d))


def _devices_event(snap: dict[str, DeviceInfo]) -> str:
    """One SSE ``data:`` frame carrying a DevicesResponse."""
    payload = DevicesResponse(data=[_to_schema(d) for d in snap.values()])
    return f"data: {payload.model_dump_json()}\n\n"


async def _device_stream(feed: DeviceFeed) -> AsyncIterator[str]:
    """Infinite SSE generator: initial current snapshot, then each refresh."""
    q = feed.subscribe()
    try:
        yield _devices_event(feed.current_snapshot())   # immediate, so the bar isn't empty
        while True:
            yield _devices_event(await q.get())
    finally:
        feed.unsubscribe(q)


def register_devices_routes(router: APIRouter) -> None:
    @router.get("/devices/stream")
    async def stream_devices(request: Request) -> StreamingResponse:
        feed: DeviceFeed = request.app.state.device_feed
        return StreamingResponse(_device_stream(feed), media_type="text/event-stream")
