"""GET /api/devices (one-shot) + the device SSE stream generator."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from llm_manager.devices import DeviceInfo
from llm_manager.gateway.api.devices import _device_stream, register_devices_routes
from llm_manager.realtime import DeviceFeed


class _FakeMonitor:
    def __init__(self) -> None:
        self.calls = 0

    def refresh(self) -> None:
        self.calls += 1

    def snapshot(self) -> dict[str, DeviceInfo]:
        return {"GPU0": DeviceInfo("RTX 4060", "GPU", "VRAM", 8192, 4096, 4096, 47.0, 62.0)}


def _app() -> FastAPI:
    app = FastAPI()
    api = APIRouter(prefix="/api")
    register_devices_routes(api)
    app.include_router(api)
    app.state.device_feed = DeviceFeed(_FakeMonitor(), interval=0.01)
    return app


def test_devices_one_shot_returns_snapshot() -> None:
    with TestClient(_app()) as c:
        r = c.get("/api/devices")
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 1
    assert data[0]["device_name"] == "RTX 4060"
    assert data[0]["device_type"] == "GPU"
    assert data[0]["usage_percentage"] == 47.0
    assert data[0]["temperature_celsius"] == 62.0


async def test_device_stream_yields_initial_then_refreshed() -> None:
    """Drive the SSE generator directly (TestClient hangs on infinite streams)."""
    feed = DeviceFeed(_FakeMonitor(), interval=0.01)
    gen = _device_stream(feed)
    first = await gen.__anext__()
    assert first.startswith("data:")
    assert "RTX 4060" in first
    # subsequent frame arrives after the next refresh tick
    second = await asyncio.wait_for(gen.__anext__(), timeout=2)
    assert second.startswith("data:")
    await gen.aclose()   # triggers finally → unsubscribe → loop stops
    assert feed.subscriber_count == 0
