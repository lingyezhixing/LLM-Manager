"""Broadcaster + DeviceFeed + ModelFeed:按订阅者门控的 fan-out + 循环,用于 SSE 推送。

Broadcaster 被所有 stream 复用。DeviceFeed = 周期刷新循环(N 个查看者 = 1 次
refresh)。ModelFeed = 变更检测循环(仅当 snapshot 值变化时才发布,
合并突发)— 驱动事件驱动的模型 stream。"""

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
    bc.publish("a")  # 填满 1 槽队列
    bc.publish("b")  # 超容量 → 静默丢弃,不抛异常
    assert await asyncio.wait_for(q.get(), timeout=1) == "a"
    assert q.empty()  # "b" 已被丢弃


async def test_unsubscribe_unknown_queue_is_safe() -> None:
    bc = Broadcaster()
    bc.unsubscribe(asyncio.Queue())  # 从未订阅 → 无操作,不抛异常
    assert bc.subscriber_count == 0


# --------------------------------------------------------------------------- #
# DeviceFeed (周期刷新循环)
# --------------------------------------------------------------------------- #
class _FakeMonitor:
    """在结构上匹配 DeviceFeed 所需的 refresh + snapshot 接口。"""

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
    await asyncio.wait_for(q1.get(), timeout=1)  # 首个 tick(refresh #1)
    q2 = feed.subscribe()
    snap2 = await asyncio.wait_for(q2.get(), timeout=1)  # q2 拿到下一个 tick
    assert "GPU0" in snap2
    snap1b = await asyncio.wait_for(q1.get(), timeout=1)  # q1 也拿到该 tick
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
# ModelFeed (变更检测循环)
# --------------------------------------------------------------------------- #
class _ChangingSnap:
    """每次调用返回新 dict,使值相等性 diff 在就地修改后仍成立。"""

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
    await asyncio.wait_for(q.get(), timeout=1)  # 初始
    await asyncio.sleep(0.06)  # 多个 tick,无变化
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
    assert calls["n"] == mid  # snapshot 函数不再被调用 → 循环已停止
