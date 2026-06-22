import asyncio
import time as _time
from pathlib import Path

import pytest

from llm_manager import state
from llm_manager.config import AppConfig, ModelConfig, ProgramConfig, Scheme
from llm_manager.devices import DeviceInfo
from llm_manager.probes import ProbeResult
from llm_manager.runtime.lifecycle import Lifecycle
from llm_manager.state import ModelStatus
from llm_manager.supervisor import ProcessRecord


class FakeSupervisor:
    def __init__(self):
        self.spawned: list[list[str]] = []
        self.killed: list[int] = []
        self.next_pid = 1000
        self.alive_pids: set[int] = set()
        self.exit_cbs: dict[int, object] = {}
        self.spawn_raises: Exception | None = None

    async def spawn(self, cmd, *, shell=True, on_output=None):
        if self.spawn_raises:
            exc, self.spawn_raises = self.spawn_raises, None
            raise exc
        pid = self.next_pid
        self.next_pid += 1
        self.spawned.append(list(cmd))
        self.alive_pids.add(pid)
        return ProcessRecord(pid=pid, started_at=0.0)

    async def kill_tree(self, pid):
        self.killed.append(pid)
        self.alive_pids.discard(pid)
        return True

    async def terminate(self, pid, timeout=10.0):
        return await self.kill_tree(pid)

    def alive(self, pid):
        return pid in self.alive_pids

    def on_exit(self, pid, cb):
        self.exit_cbs[pid] = cb

    def trigger_exit(self, pid, code=-1):
        self.alive_pids.discard(pid)
        cb = self.exit_cbs.get(pid)
        if cb:
            cb(code)


def _dev(name, avail, total=8192):
    return DeviceInfo(name, "GPU", "VRAM", total, avail, total - avail, 0.0, None)


class FakeDevices:
    def __init__(self, online=None, snap=None):
        self._online = set(online) if online else {"rtx 4060"}
        self._snap = dict(snap) if snap is not None else {"rtx 4060": _dev("rtx 4060", 8192)}
        self.freed_mb: dict[str, int] = {}    # dev -> extra available after kills

    def online_devices(self):
        return set(self._online)

    def snapshot(self):
        out = {}
        for dev, info in self._snap.items():
            freed = self.freed_mb.get(dev, 0)
            if freed:
                out[dev] = DeviceInfo(info.device_name, info.device_type, info.memory_type,
                                      info.total_memory_mb, info.available_memory_mb + freed,
                                      max(0, info.used_memory_mb - freed), info.usage_percentage,
                                      info.temperature_celsius)
            else:
                out[dev] = info
        return out

    def refresh(self):
        pass


def _model(name="m1", mode="Chat", port=8000, dev="rtx 4060", mem=2048):
    return ModelConfig(
        primary_name=name, aliases=frozenset({name}), mode=mode, port=port,
        schemes={"s": Scheme(config_source="s", required_devices=frozenset({dev}),
                             script_path=Path("run.cmd"), memory_mb={dev: mem})},
    )


def _cfg(*models):
    return AppConfig(
        program=ProgramConfig(host="127.0.0.1", port=8080, alive_time=60, log_level="INFO"),
        models={m.primary_name: m for m in models}, wol=None, claude_configs={},
    )


def _ok_probe(alias, port, start_time=None, timeout=60):
    return ProbeResult(True, "ok")


def _make(sup=None, dev=None, probes=None, models=None):
    sup = sup or FakeSupervisor()
    dev = dev or FakeDevices()
    probes = probes if probes is not None else {"Chat": _ok_probe}
    cfg = _cfg(*(models if models is not None else [_model()]))
    return Lifecycle(cfg=cfg, supervisor=sup, devices=dev, probes=probes), sup, dev, cfg


@pytest.fixture(autouse=True)
def _reset():
    state._reset()
    yield
    state._reset()


# ---------- Task 7: cold start ----------
async def test_cold_start_reaches_routing():
    life, sup, dev, cfg = _make()
    status = await life.ensure_running("m1")
    assert status == ModelStatus.ROUTING
    assert len(sup.spawned) == 1
    assert state.get_pid("m1") == 1000
    assert "m1" in life._active_schemes
    assert sup.spawned[0] == ["run.cmd"]


# ---------- Task 8: reconcile ----------
async def test_reconcile_dead_process_in_routing_marks_failed():
    life, sup, dev, cfg = _make()
    await life.ensure_running("m1")                      # ROUTING, pid 1000
    sup.alive_pids.discard(1000)                         # on_exit 漏触发:进程死但状态仍 ROUTING
    status = await life.ensure_running("m1")
    assert status == ModelStatus.ROUTING                 # reconcile 修正 FAILED 后重启到 ROUTING


async def test_reconcile_orphan_starting_with_no_inflight_marks_failed():
    life, sup, dev, cfg = _make()
    state.set_status("m1", ModelStatus.STARTING, force=True)
    assert state.has_inflight("m1") is False
    status = await life.ensure_running("m1")
    assert status == ModelStatus.ROUTING                 # reconcile→FAILED→重启


# ---------- Task 9: on_crash ----------
async def test_external_crash_marks_failed_then_restart():
    life, sup, dev, cfg = _make()
    await life.ensure_running("m1")                      # ROUTING, pid 1000
    sup.trigger_exit(1000, code=1)                       # 外部进程死亡→on_exit cb
    assert state.get_status("m1") == ModelStatus.FAILED
    assert "exited code=1" in (state.get_failure_reason("m1") or "")
    status = await life.ensure_running("m1")             # FAILED→STARTING→重启
    assert status == ModelStatus.ROUTING


async def test_cooperative_stop_exit_is_not_marked_failed():
    life, sup, dev, cfg = _make()
    await life.ensure_running("m1")
    await life.stop("m1")                                # 先 STOPPED 再 kill
    sup.trigger_exit(1000, code=0)                       # kill 触发的退出
    assert state.get_status("m1") == ModelStatus.STOPPED  # 预期退出,不转 FAILED


# ---------- Task 10: stop ----------
async def test_force_stop_then_restart_core_requirement():
    life, sup, dev, cfg = _make()
    await life.ensure_running("m1")
    assert state.get_status("m1") == ModelStatus.ROUTING
    await life.stop("m1")                                # 手动强制关闭
    assert state.get_status("m1") == ModelStatus.STOPPED
    assert state.get_pid("m1") is None
    status = await life.ensure_running("m1")             # 立即再启动——核心诉求 B3
    assert status == ModelStatus.ROUTING
    assert len(sup.spawned) == 2


async def test_stop_on_stopped_or_failed_is_noop():
    life, sup, dev, cfg = _make()
    s = await life.stop("m1")                            # 从未启动(STOPPED)
    assert s == ModelStatus.STOPPED
    assert sup.killed == []
    state.record_failure("m1", "x")                      # FAILED
    s2 = await life.stop("m1")
    assert s2 == ModelStatus.FAILED
    assert sup.killed == []


async def test_stop_from_each_running_state_lands_stopped():
    life, sup, dev, cfg = _make()
    for pre in (ModelStatus.STARTING, ModelStatus.INIT_SCRIPT,
                ModelStatus.HEALTH_CHECK, ModelStatus.ROUTING, ModelStatus.FAILED):
        state._reset()
        life._stop_events.pop("m1", None)
        life._active_schemes.pop("m1", None)
        state.set_status("m1", pre, force=True)
        s = await life.stop("m1")
        assert s == ModelStatus.STOPPED or (pre == ModelStatus.FAILED and s == ModelStatus.FAILED)


# ---------- Task 11: single-dispatch / checkpoints / errors / race / eviction ----------
async def test_single_dispatch_concurrent_start_spawns_once():
    life, sup, dev, cfg = _make()
    s1, s2 = await asyncio.gather(life.ensure_running("m1"), life.ensure_running("m1"))
    assert s1 == s2 == ModelStatus.ROUTING
    assert len(sup.spawned) == 1


async def test_stop_starting_winner_self_terminates_no_routing():
    def slow_probe(alias, port, start_time=None, timeout=60):
        _time.sleep(0.15)
        return ProbeResult(False, "slow")
    life, sup, dev, cfg = _make(probes={"Chat": slow_probe})
    task = asyncio.create_task(life.ensure_running("m1"))
    await asyncio.sleep(0.05)
    await life.stop("m1")
    status = await task
    assert status == ModelStatus.STOPPED
    assert state.get_status("m1") == ModelStatus.STOPPED


async def test_slow_probe_then_concurrent_restart_not_clobbered():
    """Blocker B: orphan winner stuck in un-interruptible probe, stop pops its
    inflight, a CONCURRENT ensure_running re-claims. Orphan winner's later
    finish_start(STOPPED) must NOT clobber the new winner (owner-token guard)."""
    def slow_probe(alias, port, start_time=None, timeout=60):
        _time.sleep(0.3)
        return ProbeResult(True, "ok")
    life, sup, dev, cfg = _make(probes={"Chat": slow_probe})
    w1 = asyncio.create_task(life.ensure_running("m1"))     # 旧 winner → fut1, 进 probe
    await asyncio.sleep(0.05)                               # 旧 winner 卡在 to_thread probe
    await life.stop("m1")                                   # STOPPED + pop_inflight(fut1)
    restart_status = await life.ensure_running("m1")        # 并发重启:新 winner fut2(快 probe)
    await w1                                                 # 旧 winner probe 返回 → finish_start(STOPPED, owner=fut1) owner-guard no-op
    assert restart_status == ModelStatus.ROUTING
    # owner-token guard 的核心验证:孤儿 winner 的 finish_start(STOPPED) no-op,
    # 绝不覆盖并发 restart winner 的 ROUTING(不变量①⑤)。若 guard 坏,这里会是 STOPPED。
    assert state.get_status("m1") == ModelStatus.ROUTING
    assert len(sup.spawned) == 2


async def test_post_spawn_stop_kills_orphan_no_leak():
    life, sup, dev, cfg = _make()
    orig_spawn = sup.spawn
    async def spy_spawn(cmd, *, shell=True, on_output=None):
        rec = await orig_spawn(cmd, shell=shell, on_output=on_output)
        life._stop_events["m1"].set()                      # 恰在 spawn 返回、临界段前
        return rec
    sup.spawn = spy_spawn
    status = await life.ensure_running("m1")
    assert status == ModelStatus.STOPPED
    assert 1000 in sup.killed


async def test_no_scheme_marks_failed():
    life, sup, dev, cfg = _make(dev=FakeDevices(online=set(), snap={}))
    status = await life.ensure_running("m1")
    assert status == ModelStatus.FAILED


async def test_insufficient_resource_marks_failed():
    dev = FakeDevices(online={"rtx 4060"}, snap={"rtx 4060": _dev("rtx 4060", 0)})
    life, sup, dev2, cfg = _make(dev=dev, models=[_model("m1", mem=4096)])
    status = await life.ensure_running("m1")
    assert status == ModelStatus.FAILED


async def test_probe_failure_marks_failed():
    def bad_probe(alias, port, start_time=None, timeout=60):
        return ProbeResult(False, "unhealthy")
    life, sup, dev, cfg = _make(probes={"Chat": bad_probe})
    status = await life.ensure_running("m1")
    assert status == ModelStatus.FAILED
    assert 1000 in sup.killed


async def test_probe_timeout_marks_failed():
    def timeout_probe(alias, port, start_time=None, timeout=60):
        _time.sleep(0.1)
        return ProbeResult(False, "探测器深层检查超时")
    life, sup, dev, cfg = _make(probes={"Chat": timeout_probe})
    status = await life.ensure_running("m1")
    assert status == ModelStatus.FAILED


async def test_probe_raising_after_spawn_kills_pid_then_failed():
    def raising_probe(alias, port, start_time=None, timeout=60):
        raise RuntimeError("probe blew up")
    life, sup, dev, cfg = _make(probes={"Chat": raising_probe})
    status = await life.ensure_running("m1")
    assert status == ModelStatus.FAILED
    assert 1000 in sup.killed          # spawned pid reaped, not orphaned (guard D)


async def test_spawn_exception_marks_failed_no_future_leak():
    life, sup, dev, cfg = _make()
    sup.spawn_raises = RuntimeError("boom")
    status = await life.ensure_running("m1")
    assert status == ModelStatus.FAILED
    assert state.has_inflight("m1") is False          # 不变量⑤:异常路径不泄漏 Future


async def test_pipeline_midstage_exception_clears_inflight():
    class BoomDevices(FakeDevices):
        def refresh(self):
            raise RuntimeError("nvidia-smi died")
    life, sup, dev, cfg = _make(dev=BoomDevices())
    status = await life.ensure_running("m1")
    assert status == ModelStatus.FAILED
    assert state.has_inflight("m1") is False


async def test_eviction_executed_then_cold_start_reaches_routing_g5():
    """G5: lifecycle 真正执行 check_and_free 决策(m1 被 stop),重快照后 m2 spawn 到 ROUTING。"""
    m1 = _model("m1", port=8000, mem=2048)
    m2 = _model("m2", port=8001, mem=4096)
    sup = FakeSupervisor()
    dev = FakeDevices(online={"rtx 4060"}, snap={"rtx 4060": _dev("rtx 4060", 2048, total=4096)})
    orig_kill = sup.kill_tree
    async def kill_releases(pid, *a, **kw):
        r = await orig_kill(pid, *a, **kw)
        dev.freed_mb["rtx 4060"] = dev.freed_mb.get("rtx 4060", 0) + 2048
        return r
    sup.kill_tree = kill_releases
    life, _, _, _ = _make(sup=sup, dev=dev, models=[m1, m2])
    await life.ensure_running("m1")                     # m1 ROUTING, pid 1000
    m1_pid = state.get_pid("m1")
    status = await life.ensure_running("m2")            # 资源不足→驱逐 m1→重快照→spawn
    assert status == ModelStatus.ROUTING
    assert m1_pid in sup.killed
    assert state.get_status("m2") == ModelStatus.ROUTING


def test_illegal_transition_raises_value_error():
    # F2: 非法转移(不经 force)抛 ValueError
    state._reset()
    state.set_status("m1", ModelStatus.STARTING, force=True)
    state.set_status("m1", ModelStatus.INIT_SCRIPT)     # STARTING→INIT_SCRIPT 合法
    with pytest.raises(ValueError):
        state.set_status("m1", ModelStatus.ROUTING)     # INIT_SCRIPT→ROUTING 非法


# ---------- Task 12: unload_all + I1 tolerance ----------
async def test_unload_all_stops_running_models():
    life, sup, dev, cfg = _make(models=[_model("m1", port=8000), _model("m2", port=8001)])
    await life.ensure_running("m1")
    await life.ensure_running("m2")
    stopped = await life.unload_all()
    assert set(stopped) == {"m1", "m2"}
    assert state.get_status("m1") == ModelStatus.STOPPED
    assert state.get_status("m2") == ModelStatus.STOPPED


async def test_unload_all_skips_already_stopped():
    life, sup, dev, cfg = _make(models=[_model("m1", port=8000)])
    stopped = await life.unload_all()
    assert stopped == []


async def test_unload_all_tolerates_one_stop_failure():
    # I1 容错:某模型 stop 抛异常时,unload_all 不整体失败、只返回成功的
    life, sup, dev, cfg = _make(models=[_model("m1", port=8000), _model("m2", port=8001)])
    await life.ensure_running("m1")
    await life.ensure_running("m2")
    bad_pid = state.get_pid("m2")
    async def kill_tree(pid):
        if pid == bad_pid:
            raise RuntimeError("kill boom")
        sup.killed.append(pid)
        sup.alive_pids.discard(pid)
        return True
    sup.kill_tree = kill_tree
    stopped = await life.unload_all()                   # 不应抛
    assert "m1" in stopped
    assert "m2" not in stopped
    assert state.get_status("m1") == ModelStatus.STOPPED


# ---------- Task 6: cancel-safe hardening ----------
async def test_ensure_running_cancelled_after_spawn_kills_pid_clears_slot():
    """cancel-safe:ensure_running 被 cancel 落在 post-spawn 阶段(spawn 后 probe 中)→
    kill_tree 被调(无孤儿)+ finish_start 清 slot(状态 FAILED、inflight 释放)+ CancelledError 传播。"""
    def slow_probe(alias, port, start_time=None, timeout=60):
        _time.sleep(0.3)
        return ProbeResult(True, "ok")
    life, sup, dev, cfg = _make(probes={"Chat": slow_probe})
    task = asyncio.create_task(life.ensure_running("m1"))
    await asyncio.sleep(0.05)                  # winner 进 spawn → HEALTH_CHECK → probe(to_thread)
    assert state.get_pid("m1") is not None     # post-spawn:pid 已记
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert 1000 in sup.killed                   # 无孤儿:post-spawn except 拓宽后 kill_tree 被调
    assert state.has_inflight("m1") is False    # slot 清(ensure_running except CancelledError → finish_start)
    assert state.get_status("m1") == ModelStatus.FAILED
