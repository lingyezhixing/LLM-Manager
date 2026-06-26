"""Broadcaster + DeviceFeed + ModelFeed: subscriber-gated fan-out + loops for SSE push.

Broadcaster is reused by all streams. DeviceFeed = periodic refresh loop (N viewers = 1
refresh). ModelFeed = change-detect loop (publishes only when the snapshot value changes,
coalescing bursts) — drives the event-driven model stream."""
from __future__ import annotations

import asyncio

from llm_manager.devices import DeviceInfo
from llm_manager.realtime import Broadcaster, DeviceFeed, ModelFeed


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
# DeviceFeed (periodic refresh loop)
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
    snap1b = await asyncio.wait_for(q1.get(), timeout=1)  # q1 also gets that tick
    assert "GPU0" in snap1b


async def test_devicefeed_loop_stops_when_last_subscriber_leaves() -> None:
    mon = _FakeMonitor()
    feed = DeviceFeed(mon, interval=0.01)
    q = feed.subscribe()
    await asyncio.wait_for(q.get(), timeout=1)
    feed.unsubscribe(q)
    assert feed.subscriber_count == 0
    await asyncio.sleep(0.08)
    mid = mon.refresh_calls
    await asyncio.sleep(0.08)
    assert mon.refresh_calls == mid


async def test_devicefeed_resubscribe_restarts_loop() -> None:
    mon = _FakeMonitor()
    feed = DeviceFeed(mon, interval=0.01)
    q1 = feed.subscribe()
    feed.unsubscribe(q1)
    q2 = feed.subscribe()
    snap = await asyncio.wait_for(q2.get(), timeout=1)
    assert "GPU0" in snap


# --------------------------------------------------------------------------- #
# ModelFeed (change-detect loop)
# --------------------------------------------------------------------------- #
class _ChangingSnap:
    """Returns a fresh dict each call so value-equality diff survives in-place mutation."""
    def __init__(self) -> None:
        self.v = 0

    def __call__(self) -> dict:
        return {"v": self.v}

    def set(self, v: int) -> None:
        self.v = v


async def test_modelfeed_publishes_initial_on_subscribe() -> None:
    snap = _ChangingSnap()
    feed = ModelFeed(snap, interval=0.01)
    q = feed.subscribe()
    first = await asyncio.wait_for(q.get(), timeout=1)
    assert first == {"v": 0}


async def test_modelfeed_silent_when_unchanged() -> None:
    snap = _ChangingSnap()
    feed = ModelFeed(snap, interval=0.01)
    q = feed.subscribe()
    await asyncio.wait_for(q.get(), timeout=1)   # initial
    await asyncio.sleep(0.06)                     # several ticks, no change
    assert q.empty()


async def test_modelfeed_publishes_on_change() -> None:
    snap = _ChangingSnap()
    feed = ModelFeed(snap, interval=0.01)
    q = feed.subscribe()
    await asyncio.wait_for(q.get(), timeout=1)
    snap.set(7)
    second = await asyncio.wait_for(q.get(), timeout=1)
    assert second == {"v": 7}


async def test_modelfeed_loop_stops_when_last_subscriber_leaves() -> None:
    calls = {"n": 0}
    base = _ChangingSnap()

    def snap() -> dict:
        calls["n"] += 1
        return base()

    feed = ModelFeed(snap, interval=0.01)
    q = feed.subscribe()
    await asyncio.wait_for(q.get(), timeout=1)
    feed.unsubscribe(q)
    await asyncio.sleep(0.06)
    mid = calls["n"]
    await asyncio.sleep(0.06)
    assert calls["n"] == mid   # snapshot fn no longer called → loop stopped
