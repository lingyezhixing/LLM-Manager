"""GET /api/devices/stream (SSE live push) for the device bar.

On connect it sends the current snapshot immediately, then each refresh from the
subscriber-gated ``DeviceFeed`` (2s). Pydantic schemas → OpenAPI (types hand-mirrored
in ``frontend/src/lib/api/models.ts``). The SSE generator is extracted (``_device_stream``)
so it can be unit-tested directly without the HTTP stack.
"""

from __future__ import annotations

import asyncio
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
    freq_mhz: float | None = None  # 与 DeviceInfo 同步:asdict 展开要求字段存在
    power_watts: float | None = None


class DevicesResponse(BaseModel):
    data: list[DeviceInfoResponse]


def _to_schema(d: DeviceInfo) -> DeviceInfoResponse:
    return DeviceInfoResponse(**asdict(d))


async def _device_stream(feed: DeviceFeed) -> AsyncIterator[str]:
    """Infinite SSE generator: initial current snapshot, then each refresh."""
    q = feed.subscribe()
    try:
        snap = feed.current_snapshot()
        # immediate, so the list isn't empty
        yield sse_frame(DevicesResponse(data=[_to_schema(d) for d in snap.values()]))
        while True:
            snap = await q.get()
            yield sse_frame(DevicesResponse(data=[_to_schema(d) for d in snap.values()]))
    finally:
        feed.unsubscribe(q)


def register_devices_routes(router: APIRouter) -> None:
    @router.get("/devices")
    async def list_devices(request: Request) -> DevicesResponse:
        """一次性快照(设备名/温度/显存),**不启动** DeviceFeed 刷新门控——零额外
        采样开销。复用缓存快照(app 启动/auto_start/模型启停时已刷新);仅当快照
        为空(理论:启动后从未有人看过设备栏)才刷一次 monitor 兜底。"""
        feed: DeviceFeed = request.app.state.device_feed
        snap = feed.current_snapshot()
        if not snap:
            monitor = request.app.state.monitor
            await asyncio.to_thread(monitor.refresh)
            snap = monitor.snapshot()
        return DevicesResponse(data=[_to_schema(d) for d in snap.values()])

    @router.get("/devices/stream")
    async def stream_devices(request: Request) -> StreamingResponse:
        feed: DeviceFeed = request.app.state.device_feed
        return StreamingResponse(_device_stream(feed), media_type="text/event-stream")
