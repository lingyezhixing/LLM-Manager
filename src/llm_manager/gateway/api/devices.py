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
from llm_manager.gateway.api.common import sse_frame
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


async def _device_stream(feed: DeviceFeed) -> AsyncIterator[str]:
    """Infinite SSE generator: initial current snapshot, then each refresh."""
    q = feed.subscribe()
    try:
        snap = feed.current_snapshot()
        # immediate frame, so the bar isn't empty
        yield sse_frame(DevicesResponse(data=[_to_schema(d) for d in snap.values()]))
        while True:
            snap = await q.get()
            yield sse_frame(DevicesResponse(data=[_to_schema(d) for d in snap.values()]))
    finally:
        feed.unsubscribe(q)


def register_devices_routes(router: APIRouter) -> None:
    @router.get("/devices/stream")
    async def stream_devices(request: Request) -> StreamingResponse:
        feed: DeviceFeed = request.app.state.device_feed
        return StreamingResponse(_device_stream(feed), media_type="text/event-stream")
