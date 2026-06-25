"""Broadcaster + DeviceFeed: subscriber-gated fan-out + 2s refresh loop for SSE push.

Broadcaster is reused by device / model / request / live-log streams. DeviceFeed wraps
a DeviceMonitor: one refresh task feeds all viewers (N viewers = 1 refresh / interval),
gated so the expensive nvidia-smi/LHM sampling only runs while someone is watching."""
from __future__ import annotations

import asyncio

from llm_manager.devices import DeviceInfo
from llm_manager.realtime import Broadcaster, DeviceFeed


# --------------------------------------------------------------------------- #
# Broadcaster
# --------------------------------------------------------------------------- #
async def test_subscribe_returns_queue_and_increments_count() -> None:
    bc = Broadcaster()
    q = bc.subscribe()
    assert isinstance(q, asyncio.Queue)
    assert bc.subscriber_count == 1


async def test_publish_delivers_to_all_subscribers() -> None:
    bc = Broadcaster()
    q1, q2 = bc.subscribe(), bc.subscribe()
    bc.publish({"x": 1})
    assert await asyncio.wait_for(q1.get(), timeout=1) == {"x": 1}
    assert await asyncio.wait_for(q2.get(), timeout=1) == {"x": 1}


async def test_unsubscribe_stops_delivery_and_decrements() -> None:
    bc = Broadcaster()
    q = bc.subscribe()
    bc.unsubscribe(q)
    assert bc.subscriber_count == 0
    bc.publish({"x": 1})
    assert q.empty()


async def test_publish_drops_when_full_without_raising() -> None:
    bc = Broadcaster(maxsize=1)
    q = bc.subscribe()
    bc.publish("a")       # fills the 1-slot queue
    bc.publish("b")       # over capacity → silently dropped, no raise
    assert await asyncio.wait_for(q.get(), timeout=1) == "a"
    assert q.empty()      # "b" was dropped


async def test_unsubscribe_unknown_queue_is_safe() -> None:
    bc = Broadcaster()
    bc.unsubscribe(asyncio.Queue())   # never subscribed → no-op, no raise
    assert bc.subscriber_count == 0


# --------------------------------------------------------------------------- #
# DeviceFeed
# --------------------------------------------------------------------------- #
class _FakeMonitor:
    """Structurally matches the refresh+snapshot surface DeviceFeed needs."""
    def __init__(self) -> None:
        self.refresh_calls = 0

    def refresh(self) -> None:
        self.refresh_calls += 1

    def snapshot(self) -> dict[str, DeviceInfo]:
        return {"GPU0": DeviceInfo("GPU0", "GPU", "VRAM", 8192, 4096, 4096, 50.0, 60.0)}


async def test_devicefeed_first_subscribe_publishes_snapshot() -> None:
    mon = _FakeMonitor()
    feed = DeviceFeed(mon, interval=0.01)
    q = feed.subscribe()
    snap = await asyncio.wait_for(q.get(), timeout=1)
    assert "GPU0" in snap
    assert mon.refresh_calls >= 1


async def test_devicefeed_second_subscriber_receives_subsequent_tick() -> None:
    mon = _FakeMonitor()
    feed = DeviceFeed(mon, interval=0.01)
    q1 = feed.subscribe()
    await asyncio.wait_for(q1.get(), timeout=1)   # first tick (refresh #1)
    q2 = feed.subscribe()
    snap2 = await asyncio.wait_for(q2.get(), timeout=1)   # q2 gets next tick
    assert "GPU0" in snap2
    # q1 also receives that same subsequent tick (one loop feeds both)
    snap1b = await asyncio.wait_for(q1.get(), timeout=1)
    assert "GPU0" in snap1b


async def test_devicefeed_loop_stops_when_last_subscriber_leaves() -> None:
    mon = _FakeMonitor()
    feed = DeviceFeed(mon, interval=0.01)
    q = feed.subscribe()
    await asyncio.wait_for(q.get(), timeout=1)
    feed.unsubscribe(q)
    assert feed.subscriber_count == 0
    await asyncio.sleep(0.08)                # let any in-flight refresh finish
    mid = mon.refresh_calls
    await asyncio.sleep(0.08)                # ~8 more ticks would fire if still running
    assert mon.refresh_calls == mid          # no climb → loop truly stopped


async def test_devicefeed_resubscribe_restarts_loop() -> None:
    mon = _FakeMonitor()
    feed = DeviceFeed(mon, interval=0.01)
    q1 = feed.subscribe()
    feed.unsubscribe(q1)
    q2 = feed.subscribe()
    snap = await asyncio.wait_for(q2.get(), timeout=1)
    assert "GPU0" in snap
