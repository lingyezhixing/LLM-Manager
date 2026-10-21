"""请求级内存注册表:活动/已取消常驻,其余终态按每模型滚动 n 条。

不写 DB、不碰 sqlite —— 全部是事件循环线程内的普通可变对象(AGENTS 不变量 2)。
跨进程无意义:注册表挂 ``app.state``,随 worker 进程生死。

终态契约:``finish()`` 同步、幂等,必须在任何 ``await`` 之前调用 —— 客户端断连时
``finally`` 里第一个 await 会立刻抛 CancelledError,晚写终态就永远写不进去。
"""

from __future__ import annotations

import asyncio
import time
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from typing import Literal, Protocol

from llm_manager.realtime import Broadcaster


class _Closable(Protocol):
    """上游响应只需要这一个能力(httpx.Response 结构性满足)。"""

    async def aclose(self) -> None: ...


class RequestStatus(str, Enum):
    RUNNING = "running"
    CANCELLING = "cancelling"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    CLIENT_CLOSED = "client_closed"
    FAILED = "failed"


TERMINAL_STATUSES: frozenset[RequestStatus] = frozenset(
    {
        RequestStatus.COMPLETED,
        RequestStatus.CANCELLED,
        RequestStatus.CLIENT_CLOSED,
        RequestStatus.FAILED,
    }
)

# 列表卡上认"哪条在鬼打墙"靠这段预览,不靠 id。
MAX_PREVIEW_CHARS = 300

# 详情事件队列容量:合批刷新的前端每 100ms 取一次,1024 足够吸收突发;满了丢最旧之外
# 的语义由 Broadcaster 决定(满即丢),前端用 seq 跳号兜底重取。
_EVENT_QUEUE_SIZE = 1024


@dataclass
class RequestInfo:
    """请求元数据。**不含输出体量** —— 字符/字节/token 一概不统计。"""

    id: str
    model: str  # primary name(内部键)
    alias: str  # aliases[0](下游 served name / 展示名)
    method: str
    path: str
    stream: bool
    status: RequestStatus
    started_at: float
    finished_at: float | None = None
    request_preview: str = ""
    request_body: dict | None = None  # 客户端原始 body 的脱敏副本
    input_text: str | None = None  # 非 JSON body 的前 4 KB 原文
    finish_reason: str | None = None
    http_status: int | None = None
    error: str | None = None
    has_reasoning: bool = False
    has_tool_calls: bool = False
    usage: dict | None = None


@dataclass(frozen=True)
class RequestEvent:
    """详情 SSE 的增量事件。``state`` 帧不带负载 —— 订阅方从 ``LiveRequest.info`` 取。"""

    type: Literal["delta", "state"]
    seq: int = 0
    text: str = ""


@dataclass
class _Runtime:
    """请求的运行时状态(与可序列化的 RequestInfo 分开,避免把 httpx 对象混进响应模型)。"""

    text: str = ""
    seq: int = 0
    cancel_requested: bool = False
    client_closed: bool = False
    finalizing: bool = False
    upstream: _Closable | None = None  # httpx.Response(结构性满足 _Closable)
    cancel_event: asyncio.Event = field(default_factory=asyncio.Event)


class LiveRequest:
    """单条请求:文本按单根字符串原地累加,事件经 Broadcaster 扇出。"""

    def __init__(
        self,
        info: RequestInfo,
        on_terminal: Callable[[LiveRequest, RequestStatus], None],
    ) -> None:
        self.info = info
        self._on_terminal = on_terminal
        self._bc: Broadcaster[RequestEvent] = Broadcaster(maxsize=_EVENT_QUEUE_SIZE)
        self._rt = _Runtime()

    # ---------- 只读便捷属性 ----------

    @property
    def text(self) -> str:
        return self._rt.text

    @property
    def seq(self) -> int:
        return self._rt.seq

    @property
    def cancel_requested(self) -> bool:
        return self._rt.cancel_requested

    @property
    def client_closed(self) -> bool:
        return self._rt.client_closed

    @property
    def finalizing(self) -> bool:
        return self._rt.finalizing

    # ---------- 抓取写入(全部同步) ----------

    def append_text(self, delta: str) -> None:
        if not delta:
            return
        self._rt.text += delta
        self._rt.seq += 1
        self._bc.publish(RequestEvent(type="delta", seq=self._rt.seq, text=delta))

    def set_http_status(self, status: int) -> None:
        self.info.http_status = status

    def set_usage(self, usage: dict | None) -> None:
        self.info.usage = usage

    def set_finish_reason(self, reason: str | None) -> None:
        self.info.finish_reason = reason

    def set_flags(self, *, has_reasoning: bool, has_tool_calls: bool) -> None:
        self.info.has_reasoning = has_reasoning
        self.info.has_tool_calls = has_tool_calls

    def attach_upstream(self, resp: _Closable) -> None:
        self._rt.upstream = resp
        status = getattr(resp, "status_code", None)
        if isinstance(status, int):
            self.info.http_status = status

    # ---------- 取消 / 断连 ----------

    async def cancel(self) -> bool:
        """受理返回 True;非 RUNNING 或已进入收尾返回 False(CANCELLING 期间幂等拒绝)。"""
        if self.info.status is not RequestStatus.RUNNING or self._rt.finalizing:
            return False
        self.info.status = RequestStatus.CANCELLING
        self._rt.cancel_requested = True
        self._rt.cancel_event.set()
        self._bc.publish(RequestEvent(type="state"))
        await self.abort_upstream()
        return True

    async def wait_cancelled(self) -> None:
        await self._rt.cancel_event.wait()

    def mark_client_closed(self) -> None:
        self._rt.client_closed = True

    async def abort_upstream(self) -> None:
        """幂等关闭上游连接;无上游时 no-op。"""
        resp = self._rt.upstream
        self._rt.upstream = None
        if resp is None:
            return
        with suppress(BaseException):
            await resp.aclose()

    # ---------- 收尾 ----------

    def begin_finalize(self) -> None:
        self._rt.finalizing = True

    def finish(self, status: RequestStatus, *, error: str | None = None) -> None:
        """同步、幂等:先到的终态生效。必须在任何 await 之前调用。"""
        if status not in TERMINAL_STATUSES:
            raise ValueError(f"not a terminal status: {status}")
        if self.info.status in TERMINAL_STATUSES:
            return
        self.info.status = status
        self.info.finished_at = time.time()
        if error is not None:
            self.info.error = error
        self._bc.publish(RequestEvent(type="state"))
        self._on_terminal(self, status)

    # ---------- 订阅 ----------

    def subscribe(self) -> asyncio.Queue[RequestEvent]:
        return self._bc.subscribe()

    def unsubscribe(self, q: asyncio.Queue[RequestEvent]) -> None:
        self._bc.unsubscribe(q)

    def tail(self, limit: int) -> tuple[str, bool]:
        """(尾部文本, 是否被截断)。服务端保留全量,只是不全发给浏览器。"""
        if limit <= 0 or len(self._rt.text) <= limit:
            return self._rt.text, False
        return self._rt.text[-limit:], True


class RequestRegistry:
    """进程内请求簿记。``retention`` 是惰性读取的 n(热配置,每次终态现读)。"""

    def __init__(self, retention: Callable[[], int]) -> None:
        self._retention = retention
        self._live: dict[str, LiveRequest] = {}
        self._rolling: dict[str, deque[str]] = {}  # model -> 终态 id(新→旧)
        self._seq = 0
        self._on_terminal_cb = self._register_terminal

    # ---------- 生命周期 ----------

    def start(
        self,
        *,
        model: str,
        alias: str,
        method: str,
        path: str,
        stream: bool,
        request_preview: str,
        request_body: dict | None = None,
        input_text: str | None = None,
    ) -> LiveRequest:
        self._seq += 1
        info = RequestInfo(
            id=f"r-{self._seq:06d}",
            model=model,
            alias=alias,
            method=method,
            path=path,
            stream=stream,
            status=RequestStatus.RUNNING,
            started_at=time.time(),
            request_preview=request_preview[:MAX_PREVIEW_CHARS],
            request_body=request_body,
            input_text=input_text,
        )
        live = LiveRequest(info, self._on_terminal_cb)
        self._live[info.id] = live
        return live

    def get(self, rid: str) -> LiveRequest | None:
        return self._live.get(rid)

    def snapshot(self) -> list[RequestInfo]:
        running = sorted(
            (lv for lv in self._live.values() if lv.info.status in _ACTIVE),
            key=lambda lv: lv.info.started_at,
        )
        cancelled = sorted(
            (lv for lv in self._live.values() if lv.info.status is RequestStatus.CANCELLED),
            key=_finished_desc,
        )
        others = sorted(
            (
                lv
                for lv in self._live.values()
                if lv.info.status not in _ACTIVE and lv.info.status is not RequestStatus.CANCELLED
            ),
            key=_finished_desc,
        )
        return [lv.info for lv in (*running, *cancelled, *others)]

    async def cancel(self, rid: str) -> bool:
        live = self._live.get(rid)
        if live is None:
            return False
        return await live.cancel()

    def clear_cancelled(self, model: str | None = None) -> int:
        doomed = [
            rid
            for rid, lv in self._live.items()
            if lv.info.status is RequestStatus.CANCELLED
            and (model is None or model in (lv.info.model, lv.info.alias))
        ]
        for rid in doomed:
            del self._live[rid]
        return len(doomed)

    def reset(self) -> None:
        """测试辅助:清空全部状态。"""
        self._live.clear()
        self._rolling.clear()
        self._seq = 0

    # ---------- 终态登记 ----------

    def _register_terminal(self, live: LiveRequest, status: RequestStatus) -> None:
        """唯一终态登记入口(由 LiveRequest.finish 同步回调)。"""
        info = live.info
        if status is RequestStatus.CANCELLED:
            return  # 已取消常驻,不参与滚动
        n = max(1, self._retention())
        bucket = self._rolling.setdefault(info.model, deque())
        bucket.append(info.id)
        while len(bucket) > n:
            evicted = bucket.popleft()
            self._live.pop(evicted, None)


_ACTIVE = frozenset({RequestStatus.RUNNING, RequestStatus.CANCELLING})


def _finished_desc(lv: LiveRequest) -> float:
    return -(lv.info.finished_at or 0.0)
