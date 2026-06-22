import asyncio
import logging
import time

import pytest

from llm_manager import state
from llm_manager.runtime import background
from llm_manager.state import ModelStatus


class _FakeLife:
    """记录 stop/ensure_running 调用,支持注入副作用(如 stop(a) 内 inc_pending(b))。"""
    def __init__(self, stop=None, ensure_running=None):
        self.stopped: list[str] = []
        self.started: list[str] = []
        self._stop_fn = stop
        self._ensure_fn = ensure_running

    async def stop(self, name):
        state.set_status(name, ModelStatus.STOPPED, force=True)  # mirror real lifecycle.stop(lifecycle.py:70):使模型退出 routing_names,防 loop 每 tick 重复 stop
        self.stopped.append(name)
        if self._stop_fn is not None:
            r = self._stop_fn(name)
            if asyncio.iscoroutine(r):
                await r

    async def ensure_running(self, name):
        self.started.append(name)
        if self._ensure_fn is not None:
            r = self._ensure_fn(name)
            if asyncio.iscoroutine(r):
                r = await r
            return r
        return ModelStatus.ROUTING


@pytest.fixture(autouse=True)
def _reset():
    state._reset()
    yield
    state._reset()


# ---------- select_idle_candidates ----------
def test_select_idle_candidates_routing_idle_no_pending():
    state.set_status("m", ModelStatus.ROUTING, force=True)
    state._set_last_access("m", time.monotonic() - 120)
    assert background.select_idle_candidates(60, time.monotonic()) == ["m"]


def test_select_idle_candidates_excludes_non_routing():
    state.set_status("s", ModelStatus.STARTING, force=True)
    state._set_last_access("s", time.monotonic() - 120)
    assert background.select_idle_candidates(60, time.monotonic()) == []


def test_select_idle_candidates_excludes_pending():
    state.set_status("m", ModelStatus.ROUTING, force=True)
    state._set_last_access("m", time.monotonic() - 120)
    state.inc_pending("m")
    assert background.select_idle_candidates(60, time.monotonic()) == []


def test_select_idle_candidates_excludes_not_yet_idle():
    state.set_status("m", ModelStatus.ROUTING, force=True)
    state._set_last_access("m", time.monotonic())  # 刚访问,未超时
    assert background.select_idle_candidates(60, time.monotonic()) == []


# ---------- idle_reclamation_loop ----------
async def test_idle_loop_reclaims_stale_routing():
    state.set_status("m", ModelStatus.ROUTING, force=True)
    state._set_last_access("m", time.monotonic() - 120)
    life = _FakeLife()
    ev = asyncio.Event()
    task = asyncio.create_task(background.idle_reclamation_loop(life, 60, ev, period=0.01))
    await asyncio.sleep(0.05)
    ev.set()
    await task
    assert life.stopped == ["m"]


async def test_idle_loop_skips_when_pending_at_scan():
    state.set_status("m", ModelStatus.ROUTING, force=True)
    state._set_last_access("m", time.monotonic() - 120)
    state.inc_pending("m")
    life = _FakeLife()
    ev = asyncio.Event()
    task = asyncio.create_task(background.idle_reclamation_loop(life, 60, ev, period=0.01))
    await asyncio.sleep(0.05)
    ev.set()
    await task
    assert life.stopped == []


async def test_idle_loop_double_check_skips_new_pending():
    # a、b 都超时;stop(a) 期间 b 来请求(inc_pending b)→ 处理 b 时二次确认跳过
    state.set_status("a", ModelStatus.ROUTING, force=True)
    state.set_status("b", ModelStatus.ROUTING, force=True)
    state._set_last_access("a", time.monotonic() - 120)
    state._set_last_access("b", time.monotonic() - 120)

    def stop_during_a(name):
        if name == "a":
            state.inc_pending("b")

    life = _FakeLife(stop=stop_during_a)
    ev = asyncio.Event()
    task = asyncio.create_task(background.idle_reclamation_loop(life, 60, ev, period=0.01))
    await asyncio.sleep(0.05)
    ev.set()
    await task
    assert "a" in life.stopped
    assert "b" not in life.stopped


async def test_idle_loop_disabled_when_alive_sec_le_zero():
    state.set_status("m", ModelStatus.ROUTING, force=True)
    state._set_last_access("m", time.monotonic() - 120)
    life = _FakeLife()
    ev = asyncio.Event()
    await background.idle_reclamation_loop(life, 0, ev, period=0.01)
    assert life.stopped == []


async def test_idle_loop_survives_stop_exception(caplog):
    state.set_status("m", ModelStatus.ROUTING, force=True)
    state._set_last_access("m", time.monotonic() - 120)

    def boom(name):
        raise RuntimeError("stop failed")

    life = _FakeLife(stop=boom)
    ev = asyncio.Event()
    task = asyncio.create_task(background.idle_reclamation_loop(life, 60, ev, period=0.01))
    await asyncio.sleep(0.05)
    ev.set()
    await task  # 不崩
    assert life.stopped == ["m"]
