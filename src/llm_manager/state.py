"""State: model lifecycle status machine + activity + single-dispatch start dedup.

Module-level functions over a private dict. asyncio single-thread event loop →
loop-resident state needs no locks (cross-thread resources like sqlite are
locked separately, see spec §6)."""
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
    last_access: float = 0.0
    pending: int = 0
    pid: int | None = None


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


def set_status(name: str, status: ModelStatus, *, reason: str | None = None, force: bool = False) -> None:
    rec = _rec(name)
    if not force and status not in _ALLOWED.get(rec.status, frozenset()):
        raise ValueError(f"Illegal transition {rec.status.value}->{status.value} for '{name}'")
    rec.status = status
    if status == ModelStatus.FAILED:
        rec.failure_reason = reason
    if status == ModelStatus.ROUTING:
        rec.last_access = time.monotonic()


def is_starting(name: str) -> bool:
    return get_status(name) in (ModelStatus.STARTING, ModelStatus.INIT_SCRIPT, ModelStatus.HEALTH_CHECK)


def is_runnable(name: str) -> bool:
    return get_status(name) == ModelStatus.ROUTING


def is_failed(name: str) -> bool:
    return get_status(name) == ModelStatus.FAILED


def record_failure(name: str, reason: str) -> None:
    rec = _rec(name)
    rec.status = ModelStatus.FAILED
    rec.failure_reason = reason


def touch_activity(name: str) -> None:
    _rec(name).last_access = time.monotonic()


def pending_count(name: str) -> int:
    return _rec(name).pending


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
    _rec(name).status = ModelStatus.STARTING
    return fut, True


def finish_start(name: str, status: ModelStatus, *, owner: asyncio.Future | None = None) -> None:
    """Winner calls this when the pipeline ends (ROUTING/FAILED/STOPPED).

    owner: the future this winner obtained from claim_start. If given and the
    current _inflight[name] is a DIFFERENT future (stop already popped ours, or
    a concurrent restart re-claimed), this call is a no-op — we must NOT clobber
    the new owner's inflight or overwrite rec.status. Guards invariant 5
    against the slow-probe + concurrent-restart interleaving (orphan winner)."""
    rec = _rec(name)
    if owner is not None and _inflight.get(name) is not owner:
        return
    fut = _inflight.pop(name, None)
    rec.status = status
    if status == ModelStatus.FAILED and rec.failure_reason is None:
        rec.failure_reason = "startup failed"
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
