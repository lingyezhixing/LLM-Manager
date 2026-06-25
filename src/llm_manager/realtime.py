"""Realtime push infrastructure: subscriber-gated fan-out + device refresh loop for SSE.

``Broadcaster`` is a generic many-listener event bus (each subscriber gets its own
``asyncio.Queue``; ``publish()`` fans to all, drop-on-full for slow consumers).
``DeviceFeed`` wraps a device monitor with a subscriber-gated refresh loop: one refresh
task feeds every viewer (N viewers = 1 refresh / interval), and the loop only runs while
someone is subscribed — so the expensive nvidia-smi / LHM sampling never runs unattended.

These are the codebase's first management-class streaming primitives; the request-monitor
and live-log SSE endpoints will build on the same ``Broadcaster``. Loop-resident
(asyncio single-thread) → no locks on the subscriber set.
"""
from __future__ import annotations

import asyncio
from typing import Generic, Protocol, TypeVar

from llm_manager.devices import DeviceInfo

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


class _SnapshotSource(Protocol):
    """Minimal refresh+snapshot surface; DeviceMonitor satisfies it structurally."""
    def refresh(self) -> None: ...
    def snapshot(self) -> dict[str, DeviceInfo]: ...


class DeviceFeed:
    """Subscriber-gated periodic device-snapshot feed for ``GET /api/devices/stream``.

    First subscriber starts the refresh loop; last unsubscribe stops it. The loop
    refreshes the monitor OFF the event loop (``asyncio.to_thread`` — nvidia-smi / LHM
    are blocking) and publishes each snapshot to all subscribers, so N viewers share a
    single refresh per interval.
    """

    def __init__(self, monitor: _SnapshotSource, interval: float = 2.0) -> None:
        self._monitor = monitor
        self._bc: Broadcaster[dict[str, DeviceInfo]] = Broadcaster()
        self._interval = interval
        self._task: asyncio.Task[None] | None = None

    def subscribe(self) -> asyncio.Queue[dict[str, DeviceInfo]]:
        q = self._bc.subscribe()
        if self._task is None:
            self._task = asyncio.create_task(self._loop())
        return q

    def unsubscribe(self, q: asyncio.Queue[dict[str, DeviceInfo]]) -> None:
        self._bc.unsubscribe(q)
        if self._bc.subscriber_count == 0 and self._task is not None:
            self._task.cancel()
            self._task = None

    @property
    def subscriber_count(self) -> int:
        return self._bc.subscriber_count

    def current_snapshot(self) -> dict[str, DeviceInfo]:
        """Current cached snapshot (no refresh); the loop keeps it warm while subscribed."""
        return self._monitor.snapshot()

    async def _loop(self) -> None:
        try:
            while self._bc.subscriber_count > 0:
                snapshot = await asyncio.to_thread(self._refresh_and_snapshot)
                self._bc.publish(snapshot)
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            pass

    def _refresh_and_snapshot(self) -> dict[str, DeviceInfo]:
        self._monitor.refresh()
        return self._monitor.snapshot()
