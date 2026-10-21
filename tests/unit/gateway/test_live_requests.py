import asyncio

import pytest

from llm_manager.gateway.live_requests import (
    TERMINAL_STATUSES,
    RequestEvent,
    RequestRegistry,
    RequestStatus,
)


def _start(reg: RequestRegistry, *, model="m1", alias="a1", preview="hi"):
    return reg.start(
        model=model,
        alias=alias,
        method="POST",
        path="/v1/chat/completions",
        stream=True,
        request_preview=preview,
    )


def test_completed_rolls_per_model_and_keeps_newest():
    reg = RequestRegistry(retention=lambda: 2)
    ids = []
    for _ in range(3):
        live = _start(reg)
        live.finish(RequestStatus.COMPLETED)
        ids.append(live.info.id)
    other = _start(reg, model="m2", alias="a2")
    other.finish(RequestStatus.COMPLETED)

    got = {i.id for i in reg.snapshot()}
    assert ids[0] not in got
    assert ids[1] in got and ids[2] in got
    assert other.info.id in got


def test_retention_is_read_at_each_terminal_not_at_construction():
    n = {"value": 1}
    reg = RequestRegistry(retention=lambda: n["value"])
    first = _start(reg)
    first.finish(RequestStatus.COMPLETED)
    n["value"] = 2
    second = _start(reg)
    second.finish(RequestStatus.COMPLETED)

    got = {i.id for i in reg.snapshot()}
    assert {first.info.id, second.info.id} <= got


def test_cancelled_is_never_evicted_and_cleared_by_model():
    reg = RequestRegistry(retention=lambda: 1)
    a1 = _start(reg)
    a1.finish(RequestStatus.CANCELLED)
    a2 = _start(reg, model="m2", alias="a2")
    a2.finish(RequestStatus.CANCELLED)
    for _ in range(3):
        live = _start(reg)
        live.finish(RequestStatus.COMPLETED)

    got = {i.id for i in reg.snapshot()}
    assert {a1.info.id, a2.info.id} <= got

    assert reg.clear_cancelled(model="m1") == 1
    got = {i.id for i in reg.snapshot()}
    assert a1.info.id not in got and a2.info.id in got


def test_finish_is_idempotent_first_terminal_wins():
    reg = RequestRegistry(retention=lambda: 20)
    live = _start(reg)
    q = live.subscribe()
    live.finish(RequestStatus.CANCELLED)
    live.finish(RequestStatus.COMPLETED)

    assert live.info.status is RequestStatus.CANCELLED
    events = []
    while not q.empty():
        events.append(q.get_nowait())
    assert [e.type for e in events] == ["state"]


def test_tail_returns_suffix_and_truncated_flag():
    reg = RequestRegistry(retention=lambda: 20)
    live = _start(reg)
    live.append_text("abc")
    live.append_text("def")

    assert live.tail(4) == ("cdef", True)
    assert live.tail(100) == ("abcdef", False)


def test_append_text_broadcasts_delta_with_increasing_seq():
    reg = RequestRegistry(retention=lambda: 20)
    live = _start(reg)
    q = live.subscribe()
    live.append_text("x")
    live.append_text("")

    assert live.seq == 1
    ev = q.get_nowait()
    assert ev == RequestEvent(type="delta", seq=1, text="x")


async def test_cancel_only_accepts_running_and_wakes_waiter():
    reg = RequestRegistry(retention=lambda: 20)
    live = _start(reg)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(live.wait_cancelled(), 0.01)

    assert await live.cancel() is True
    assert live.info.status is RequestStatus.CANCELLING
    await asyncio.wait_for(live.wait_cancelled(), 0.5)
    assert await live.cancel() is False

    live.finish(RequestStatus.CANCELLED)
    assert await live.cancel() is False


def test_snapshot_orders_running_then_cancelled_then_others():
    reg = RequestRegistry(retention=lambda: 20)
    done = _start(reg)
    done.finish(RequestStatus.COMPLETED)
    canc = _start(reg)
    canc.finish(RequestStatus.CANCELLED)
    run = _start(reg)

    assert [i.id for i in reg.snapshot()] == [run.info.id, canc.info.id, done.info.id]


def test_terminal_statuses_is_the_expected_set():
    assert TERMINAL_STATUSES == {
        RequestStatus.COMPLETED,
        RequestStatus.CANCELLED,
        RequestStatus.CLIENT_CLOSED,
        RequestStatus.FAILED,
    }
