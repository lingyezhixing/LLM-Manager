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
from collections.abc import Callable
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


class _GatedFeed(Generic[T]):
    """Subscriber-gated periodic feed 骨架:首订阅起 task、末订阅取消,loop 只在有人
    订阅时跑。子类实现 ``_snapshot``(同步快照)、``_produce``(每 tick 生成)与
    ``_dispatch``(派发);``_on_unsubscribed`` 在末订阅退出时复位子类状态(如
    ModelFeed 的 last-seen,保证 resubscribe 重新发布首帧)。"""

    def __init__(self, interval: float) -> None:
        self._bc: Broadcaster[T] = Broadcaster()
        self._interval = interval
        self._task: asyncio.Task[None] | None = None

    def subscribe(self) -> asyncio.Queue[T]:
        q = self._bc.subscribe()
        if self._task is None:
            self._task = asyncio.create_task(self._loop())
        return q

    def unsubscribe(self, q: asyncio.Queue[T]) -> None:
        self._bc.unsubscribe(q)
        if self._bc.subscriber_count == 0 and self._task is not None:
            self._task.cancel()
            self._task = None
            self._on_unsubscribed()

    @property
    def subscriber_count(self) -> int:
        return self._bc.subscriber_count

    def current_snapshot(self) -> T:
        """当前缓存快照(不刷新);loop 在订阅期间保持其新鲜。"""
        return self._snapshot()

    def _snapshot(self) -> T:
        raise NotImplementedError

    async def _loop(self) -> None:
        try:
            while self._bc.subscriber_count > 0:
                snap = await self._produce()
                self._dispatch(snap)
                await asyncio.sleep(self._interval)
        except asyncio.CancelledError:
            pass

    async def _produce(self) -> T:
        raise NotImplementedError

    def _dispatch(self, snap: T) -> None:
        raise NotImplementedError

    def _on_unsubscribed(self) -> None:
        """末订阅退出复位(默认 no-op)。"""


class DeviceFeed(_GatedFeed[dict[str, DeviceInfo]]):
    """Subscriber-gated periodic device-snapshot feed for ``GET /api/devices/stream``.

    First subscriber starts the refresh loop; last unsubscribe stops it. The loop
    refreshes the monitor OFF the event loop (``asyncio.to_thread`` — nvidia-smi / LHM
    are blocking) and publishes each snapshot to all subscribers, so N viewers share a
    single refresh per interval.
    """

    def __init__(self, monitor: _SnapshotSource, interval: float = 2.0) -> None:
        super().__init__(interval)
        self._monitor = monitor

    def _snapshot(self) -> dict[str, DeviceInfo]:
        return self._monitor.snapshot()

    async def _produce(self) -> dict[str, DeviceInfo]:
        return await asyncio.to_thread(self._refresh_and_snapshot)

    def _dispatch(self, snap: dict[str, DeviceInfo]) -> None:
        self._bc.publish(snap)

    def _refresh_and_snapshot(self) -> dict[str, DeviceInfo]:
        self._monitor.refresh()
        return self._monitor.snapshot()


class ModelFeed(_GatedFeed[T]):
    """Subscriber-gated **change-detect** feed for value snapshots (e.g. model state).

    Polls ``snapshot()`` every ``interval`` and publishes ONLY when the value changes
    (value-equality), coalescing bursts — so the model stream is event-driven rather than
    a fixed cadence. The snapshot must exclude time-derived fields (idle/uptime) or it
    would differ every tick; the frontend ticks those locally from timestamps in the
    snapshot. First subscriber starts the loop; last unsubscribe stops it and resets the
    last-seen value so a later resubscribe re-publishes.
    """

    def __init__(self, snapshot: Callable[[], T], interval: float = 0.5) -> None:
        super().__init__(interval)
        self._snapshot_fn = snapshot
        self._last: T | None = None

    def _snapshot(self) -> T:
        return self._snapshot_fn()

    async def _produce(self) -> T:
        return self._snapshot_fn()

    def _dispatch(self, snap: T) -> None:
        if snap != self._last:
            self._last = snap
            self._bc.publish(snap)

    def _on_unsubscribed(self) -> None:
        self._last = None  # resubscribe should re-publish the initial snapshot
