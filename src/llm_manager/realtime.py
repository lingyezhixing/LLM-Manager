"""Realtime push infrastructure: subscriber-gated fan-out for SSE streams.

``Broadcaster`` is a generic many-listener event bus: each subscriber gets its own
``asyncio.Queue``; ``publish()`` fans an item to all (drop-on-full protects the loop
from a slow consumer). The device / model / request / live-log SSE endpoints build
their feeds on top of this — the codebase's first management-class streaming primitive.

Loop-resident (asyncio single-thread) → no locks needed for the subscriber set.
"""
from __future__ import annotations

import asyncio
from typing import Generic, TypeVar

T = TypeVar("T")


class Broadcaster(Generic[T]):
    """Many-listener fan-out used by SSE push endpoints."""

    def __init__(self, maxsize: int = 16) -> None:
        self._subs: set[asyncio.Queue[T]] = set()
        self._maxsize = maxsize

    def subscribe(self) -> asyncio.Queue[T]:
        """Register a new subscriber; returns its dedicated queue."""
        q: asyncio.Queue[T] = asyncio.Queue(maxsize=self._maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[T]) -> None:
        """Drop a subscriber; unknown queues are a safe no-op."""
        self._subs.discard(q)

    def publish(self, item: T) -> None:
        """Fan an item to every subscriber; full queues silently drop (slow consumer)."""
        for q in list(self._subs):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)
