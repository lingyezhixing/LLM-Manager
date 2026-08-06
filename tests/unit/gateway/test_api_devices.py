"""The device SSE stream generator + one-shot snapshot endpoint."""
from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_manager.devices import DeviceInfo
from llm_manager.gateway.api.devices import _device_stream
from llm_manager.realtime import DeviceFeed


class _FakeMonitor:
    def __init__(self, snapshot: dict[str, DeviceInfo] | None = None) -> None:
        self.calls = 0
        self._snapshot = snapshot if snapshot is not None else {
            "GPU0": DeviceInfo("RTX 4060", "GPU", "VRAM", 8192, 4096, 4096, 47.0, 62.0)}

    def refresh(self) -> None:
        self.calls += 1

    def snapshot(self) -> dict[str, DeviceInfo]:
        return self._snapshot


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


def test_list_devices_one_shot_uses_cached_snapshot() -> None:
    """GET /api/devices 返回缓存快照且不启动刷新门控(快照非空时零 refresh)。"""
    from fastapi import APIRouter

    from llm_manager.gateway.api.devices import register_devices_routes

    fake = _FakeMonitor()
    feed = DeviceFeed(fake, interval=60)
    app = FastAPI()
    app.state.monitor = fake
    app.state.device_feed = feed
    router = APIRouter(prefix="/api")
    register_devices_routes(router)
    app.include_router(router)
    client = TestClient(app)

    fake.refresh()   # 模拟 app 启动时已刷过快照
    assert fake.calls == 1
    res = client.get("/api/devices")
    assert res.status_code == 200
    body = res.json()
    assert [d["device_name"] for d in body["data"]] == ["RTX 4060"]
    assert fake.calls == 1   # 快照非空 → 零额外刷新


def test_list_devices_refreshes_when_snapshot_empty() -> None:
    """空快照(启动后从未刷新过)时兜底刷新一次 monitor。"""
    from fastapi import APIRouter

    from llm_manager.gateway.api.devices import register_devices_routes

    fake = _FakeMonitor(snapshot={})   # 空快照
    feed = DeviceFeed(fake, interval=60)
    app = FastAPI()
    app.state.monitor = fake
    app.state.device_feed = feed
    router = APIRouter(prefix="/api")
    register_devices_routes(router)
    app.include_router(router)
    client = TestClient(app)

    res = client.get("/api/devices")
    assert res.status_code == 200
    assert res.json()["data"] == []
    assert fake.calls == 1   # 空快照 → 兜底刷新一次
