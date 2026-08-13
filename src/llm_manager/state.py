"""State: model lifecycle status machine + activity + single-dispatch start dedup.

Module-level functions over a private dict. asyncio single-thread event loop →
loop-resident state needs no locks (cross-thread resources like sqlite are
locked separately)."""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum


class ModelStatus(str, Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    INIT_SCRIPT = "init_script"
    HEALTH_CHECK = "health_check"
    ROUTING = "routing"
    FAILED = "failed"


# Allowed NON-force transitions. STOPPED via force=True bypasses (cooperative stop).
_ALLOWED: dict[ModelStatus, frozenset[ModelStatus]] = {
    ModelStatus.STOPPED: frozenset({ModelStatus.STARTING}),
    ModelStatus.STARTING: frozenset({ModelStatus.INIT_SCRIPT, ModelStatus.FAILED}),
    ModelStatus.INIT_SCRIPT: frozenset({ModelStatus.HEALTH_CHECK, ModelStatus.FAILED}),
    ModelStatus.HEALTH_CHECK: frozenset({ModelStatus.ROUTING, ModelStatus.FAILED}),
    ModelStatus.ROUTING: frozenset({ModelStatus.FAILED}),
    ModelStatus.FAILED: frozenset({ModelStatus.STARTING}),
}


@dataclass
class _Record:
    status: ModelStatus = ModelStatus.STOPPED
    failure_reason: str | None = None
    last_access: float = 0.0  # monotonic — internal idle reclamation
    pending: int = 0
    pid: int | None = None
    started_at: float | None = None  # wall-clock epoch when entered ROUTING (frontend uptime)
    last_access_wall: float = 0.0  # wall-clock epoch of last activity (frontend idle)


_state: dict[str, _Record] = {}
_inflight: dict[str, asyncio.Future] = {}


def _reset() -> None:
    """Test helper: clear all state."""
    _state.clear()
    _inflight.clear()


def _rec(name: str) -> _Record:
    rec = _state.get(name)
    if rec is None:
        rec = _Record()
        _state[name] = rec
    return rec


def get_status(name: str) -> ModelStatus:
    return _rec(name).status


def set_status(
    name: str, status: ModelStatus, *, reason: str | None = None, force: bool = False
) -> None:
    rec = _rec(name)
    if not force and status not in _ALLOWED.get(rec.status, frozenset()):
        raise ValueError(f"Illegal transition {rec.status.value}->{status.value} for '{name}'")
    rec.status = status
    if status == ModelStatus.FAILED:
        rec.failure_reason = reason
    else:
        rec.failure_reason = (
            None  # 离开 FAILED(成功重启/停止)→ 清陈旧原因(B3);失败原因只在 FAILED 态有意义
        )
    if status == ModelStatus.ROUTING:
        now_wall = time.time()
        rec.last_access = time.monotonic()
        rec.last_access_wall = now_wall
        rec.started_at = now_wall
    else:
        rec.started_at = None  # uptime only while ROUTING


def is_runnable(name: str) -> bool:
    return get_status(name) == ModelStatus.ROUTING


def record_failure(name: str, reason: str) -> None:
    rec = _rec(name)
    rec.status = ModelStatus.FAILED
    rec.failure_reason = reason
    rec.pid = None  # 进程已死/将死/未spawn(所有 caller 调用时如此);清 stale pid 防 _reconcile 漏清 + 防 stop 误 kill 被复用的 pid
    rec.started_at = None  # FAILED → 无 uptime


def get_failure_reason(name: str) -> str | None:
    return _rec(name).failure_reason


def touch_activity(name: str) -> None:
    rec = _rec(name)
    rec.last_access = time.monotonic()
    rec.last_access_wall = time.time()


def get_last_access(name: str) -> float:
    return _rec(name).last_access


def get_started_at(name: str) -> float | None:
    """Wall-clock epoch when the model entered ROUTING (None when not routing). Frontend ticks uptime."""
    return _rec(name).started_at


def get_last_access_wall(name: str) -> float:
    """Wall-clock epoch of last activity (0.0 if never). Frontend ticks idle locally, no push."""
    return _rec(name).last_access_wall


def _set_last_access(name: str, ts: float) -> None:
    """Test helper:设任意 last_access(background 测试控时间相对值,同 _reset)."""
    _rec(name).last_access = ts


def pending_count(name: str) -> int:
    return _rec(name).pending


def routing_names() -> list[str]:
    """当前 ROUTING 模型名(background 空闲扫描用,单一真相源,不持 cfg.models 副本)。"""
    return [n for n, r in _state.items() if r.status == ModelStatus.ROUTING]


def record_pid(name: str, pid: int) -> None:
    _rec(name).pid = pid


def get_pid(name: str) -> int | None:
    return _rec(name).pid


def clear_pid(name: str) -> None:
    _rec(name).pid = None


def inc_pending(name: str) -> None:
    _rec(name).pending += 1


def dec_pending(name: str) -> None:
    _rec(name).pending = max(0, _rec(name).pending - 1)


def begin_request(name: str) -> None:
    inc_pending(name)
    touch_activity(name)


def end_request(name: str) -> None:
    dec_pending(name)
    touch_activity(name)


def claim_start(name: str) -> tuple[asyncio.Future, bool]:
    """Atomic single-dispatch. Returns (future, won).
    won=True → caller runs the start pipeline, then finish_start(name, status).
    won=False → caller lost: await future for the final status. Never spawns twice."""
    existing = _inflight.get(name)
    if existing is not None:
        return existing, False
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    _inflight[name] = fut
    rec = _rec(name)
    rec.status = ModelStatus.STARTING
    rec.failure_reason = None  # 新一轮启动:清上次失败原因(B3),防 SSE 携带陈旧 reason
    return fut, True


def finish_start(name: str, status: ModelStatus, *, owner: asyncio.Future | None = None) -> None:
    """Winner calls this when the pipeline ends (ROUTING/FAILED/STOPPED).

    owner: the future this winner obtained from claim_start. If given and the
    current _inflight[name] is a DIFFERENT future (stop already popped ours, or
    a concurrent restart re-claimed), this call is a no-op — we must NOT clobber
    the new owner's inflight or overwrite rec.status. Guards the owner-token
    single-dispatch invariant
    against the slow-probe + concurrent-restart interleaving (orphan winner)."""
    rec = _rec(name)
    if owner is not None and _inflight.get(name) is not owner:
        return
    fut = _inflight.pop(name, None)
    rec.status = status
    if status == ModelStatus.FAILED:
        if rec.failure_reason is None:
            rec.failure_reason = "startup failed"
    else:
        rec.failure_reason = None  # 成功(ROUTING)/STOPPED → 清陈旧失败原因(B3)
    if fut is not None and not fut.done():
        fut.set_result(status)


def has_inflight(name: str) -> bool:
    return name in _inflight


def clear_inflight(name: str) -> None:
    _inflight.pop(name, None)


def pop_inflight(name: str) -> asyncio.Future | None:
    """Atomically remove + return the inflight future (stop releases the slot
    so the model can restart immediately)."""
    return _inflight.pop(name, None)
