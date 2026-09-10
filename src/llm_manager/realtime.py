"""实时推送基础设施:订阅者门控 fan-out + 设备刷新循环,供 SSE 使用。

``Broadcaster`` 是通用的多监听者事件总线(每个订阅者有独立 ``asyncio.Queue``;
``publish()`` 全量扇出,慢消费者满队列即丢弃)。``DeviceFeed`` 用订阅者门控的
刷新循环包住设备监控器:一个刷新任务喂给所有查看者(N 个查看者 = 每间隔 1 次
刷新),且仅当有人订阅时循环才跑——昂贵的 nvidia-smi / LHM 采样不会无人值守
空转。

loop-resident(asyncio 单线程)→ 订阅者集合无需锁。
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Generic, Protocol, TypeVar

from llm_manager.devices import DeviceInfo

T = TypeVar("T")


class Broadcaster(Generic[T]):
    """多监听者扇出,供 SSE 推送端点使用。"""

    def __init__(self, maxsize: int = 16) -> None:
        self._subs: set[asyncio.Queue[T]] = set()
        self._maxsize = maxsize

    def subscribe(self) -> asyncio.Queue[T]:
        """注册新订阅者;返回其专属队列。"""
        q: asyncio.Queue[T] = asyncio.Queue(maxsize=self._maxsize)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue[T]) -> None:
        """移除订阅者;未知队列为安全 no-op。"""
        self._subs.discard(q)

    def publish(self, item: T) -> None:
        """把条目扇出给每个订阅者;满队列静默丢弃(慢消费者)。"""
        for q in list(self._subs):
            try:
                q.put_nowait(item)
            except asyncio.QueueFull:
                pass

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


class _SnapshotSource(Protocol):
    """最小 refresh+snapshot 接口;DeviceMonitor 结构性满足之。"""

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
    """供 ``GET /api/devices/stream`` 使用的订阅者门控周期设备快照 feed。

    首个订阅者启动刷新循环;末个退订停止之。循环在事件循环之外刷新监控器
    (``asyncio.to_thread`` — nvidia-smi / LHM 均为阻塞),并把每个快照发布给
    全部订阅者,故 N 个查看者共享每间隔一次刷新。
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
    """订阅者门控的 **变更检测** 值快照 feed(如模型状态)。

    每 ``interval`` 轮询 ``snapshot()``,仅当值变化时(值相等比较)才发布,合并突发
    ——模型流是事件驱动的,而非固定节拍。快照必须排除时间衍生字段(idle/uptime),
    否则每 tick 都不同;前端根据快照内时间戳在本地累加这些。首个订阅者启动循环;
    末个退订停止之并复位 last-seen 值,使后续 resubscribe 重新发布。
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
        self._last = None  # resubscribe 时应重新发布初始快照
