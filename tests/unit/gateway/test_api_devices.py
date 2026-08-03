"""The device SSE stream generator."""
from __future__ import annotations

import asyncio

from llm_manager.devices import DeviceInfo
from llm_manager.gateway.api.devices import _device_stream
from llm_manager.realtime import DeviceFeed


class _FakeMonitor:
    def __init__(self) -> None:
        self.calls = 0

    def refresh(self) -> None:
        self.calls += 1

    def snapshot(self) -> dict[str, DeviceInfo]:
        return {"GPU0": DeviceInfo("RTX 4060", "GPU", "VRAM", 8192, 4096, 4096, 47.0, 62.0)}


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
