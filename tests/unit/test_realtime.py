"""Broadcaster: subscriber-gated fan-out for SSE push.

Reused by device / model / request / live-log streams. Pure async logic —
subscribe/unsubscribe/publish, per-subscriber queue with drop-on-full."""
from __future__ import annotations

import asyncio

from llm_manager.realtime import Broadcaster


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
