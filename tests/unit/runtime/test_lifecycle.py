import asyncio
import time as _time
from collections.abc import Callable

import pytest

from llm_manager import state
from llm_manager.config import AppConfig, Command, ModelConfig, ProgramConfig, Scheme
from llm_manager.data import logs
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

    async def spawn(self, cmd, *, shell=True, on_output=None, env=None, cwd=None):
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
        primary_name=name, aliases=(name,), mode=mode, port=port,
        schemes={"s": Scheme(config_source="s", required_devices=frozenset({dev}),
                             command=Command(exe="run.cmd"), memory_mb={dev: mem})},
    )


def _cfg(*models):
    return AppConfig(
        program=ProgramConfig(host="127.0.0.1", port=8080, alive_time=60, log_level="INFO"),
        models={m.primary_name: m for m in models}, wol=None, claude_configs={},
    )


def _ok_probe(alias, port, start_time=None, timeout=60):
    return ProbeResult(True, "ok")


def _make(sup=None, dev=None, probes=None, models=None, db=None):
    sup = sup or FakeSupervisor()
    dev = dev or FakeDevices()
    probes = probes if probes is not None else {"Chat": _ok_probe}
    cfg = _cfg(*(models if models is not None else [_model()]))
    return Lifecycle(get_cfg=lambda: cfg, supervisor=sup, devices=dev, probes=probes, db=db), sup, dev, cfg


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
    async def spy_spawn(cmd, *, shell=False, on_output=None, env=None, cwd=None):
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


# ---------- Task 12: unload_all + tolerance ----------
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
    # 容错:某模型 stop 抛异常时,unload_all 不整体失败、只返回成功的
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


# ---------- Task 2 (Plan 7): spawn lock ----------
async def test_spawn_lock_serializes_concurrent_spawns():
    import time as _t
    sup = FakeSupervisor()
    spawn_log: list = []
    _real_spawn = sup.spawn

    async def logged_spawn(cmd, *, shell=False, on_output=None, env=None, cwd=None):
        spawn_log.append(("start", _t.monotonic()))
        await asyncio.sleep(0.05)  # 模拟 spawn 耗时:无锁则 a/b spawn 并行交错,有锁则串行
        rec = await _real_spawn(cmd, shell=shell, on_output=on_output)
        spawn_log.append(("end", _t.monotonic()))
        return rec
    sup.spawn = logged_spawn

    models = [_model("a", dev="rtx 4060"), _model("b", dev="780m")]
    life, _sup, _d, _c = _make(sup=sup, dev=FakeDevices(
        online={"rtx 4060", "780m"},
        snap={"rtx 4060": _dev("rtx 4060", 8192), "780m": _dev("780m", 8192)}),
        models=models, probes={"Chat": _ok_probe})
    await asyncio.gather(life.ensure_running("a"), life.ensure_running("b"))
    assert spawn_log[0][0] == "start"
    assert spawn_log[1][0] == "end"
    assert spawn_log[2][0] == "start"


async def test_spawn_lock_preserves_inflight_eviction_protection():
    # 回归:spawn 锁不破坏 inflight 保护(pending>0 不被 eviction 驱;spec §3.3)
    models = [_model("a", dev="rtx 4060", mem=4096), _model("b", dev="rtx 4060", mem=8192)]
    life, sup, _d, _c = _make(sup=FakeSupervisor(), dev=FakeDevices(
        online={"rtx 4060"}, snap={"rtx 4060": _dev("rtx 4060", 4096)}),
        models=models, probes={"Chat": _ok_probe})
    await life.ensure_running("a")
    state.begin_request("a")
    status = await life.ensure_running("b")
    assert "a" not in sup.killed
    assert status == ModelStatus.FAILED
    state.end_request("a")


async def test_ensure_running_inc_pending_closes_idle_reclaim_tocou():
    """#2:ensure_running(inc_pending=True) 在返回 ROUTING 的同一无 await 临界段内 inc pending,
    使 idle 回收 loop(查 pending==0)在 ensure_running 返回后看到 pending>=1,不会误回收在途请求的模型。"""
    life, _sup, _d, _c = _make()
    await life.ensure_running("m1")                          # 冷启动到 ROUTING(默认不 inc)
    assert state.get_status("m1") == ModelStatus.ROUTING
    assert state.pending_count("m1") == 0

    status = await life.ensure_running("m1", inc_pending=True)   # 模拟 proxy 请求
    assert status == ModelStatus.ROUTING
    assert state.pending_count("m1") == 1                    # inc 在 ensure_running 内、返回前已生效

    state.end_request("m1")                                  # proxy 完成 → dec
    assert state.pending_count("m1") == 0


async def test_ensure_running_inc_pending_skips_when_not_routing():
    """inc_pending=True 但未到 ROUTING(FAILED)→ 不 inc(proxy 走 503,无需 dec)。"""
    life, _sup, _d, _c = _make(probes={"Chat": lambda *a, **k: ProbeResult(False, "fail")})
    status = await life.ensure_running("m1", inc_pending=True)
    assert status == ModelStatus.FAILED
    assert state.pending_count("m1") == 0


# ---------- Task 3 (Plan): wire on_output to logs.capture + stop ends session ----------


class _CapturingSupervisor(FakeSupervisor):
    """FakeSupervisor that records the on_output callback lifecycle passes to spawn."""
    on_output: Callable[[str, str], None] | None

    def __init__(self):
        super().__init__()
        self.on_output = None

    async def spawn(self, cmd, *, shell=True, on_output=None, env=None, cwd=None):
        self.on_output = on_output
        return await super().spawn(cmd, shell=shell, on_output=on_output)


async def test_pipeline_wires_on_output_to_logs_capture(tmp_path):
    """spawn 时 on_output capture 接线:输出行经 capture 落库到该模型的最新会话。"""
    from llm_manager.data.persistence import open_db
    logs.reset()
    db = open_db(tmp_path / "t.db")
    logs.init(db)
    try:
        cap = _CapturingSupervisor()
        lc, sup, dev, cfg = _make(sup=cap, db=db)
        await lc.ensure_running("m1")
        assert cap.on_output is not None
        sid = logs.resolve_session("m1")
        assert sid is not None
        # 模拟 supervisor 读线程 marshal 出来的行(capture 同步,可直接调)
        cap.on_output("server listening on :8000", "out")
        cap.on_output("error: boom", "err")
        await logs.flush()
        lines = logs.log_lines_backfill(db, sid, limit=10)
        assert [line["text"] for line in lines] == ["server listening on :8000", "error: boom"]
        assert [line["level"] for line in lines] == ["ok", "error"]
    finally:
        logs.reset()


async def test_stop_ends_log_session(tmp_path):
    """stop 后会话收口:resolve_session 返回 None、DB end_time 已落库;后续 capture 被丢弃。"""
    from llm_manager.data.persistence import open_db
    logs.reset()
    db = open_db(tmp_path / "t.db")
    logs.init(db)
    try:
        cap = _CapturingSupervisor()
        lc, sup, dev, cfg = _make(sup=cap, db=db)
        await lc.ensure_running("m1")
        assert cap.on_output is not None
        cap.on_output("old session line", "out")
        sid = logs.resolve_session("m1")
        assert sid is not None
        await lc.stop("m1")
        assert logs.resolve_session("m1") is None
        rows = logs.log_sessions(db, type_="model", model_name="m1")
        assert len(rows) == 1 and rows[0]["end_time"] is not None
        # stop 后 capture 无进行中会话可入 → 丢弃(行数不变,不污染历史)
        logs.capture("m1", "dropped after stop", "out")
        await logs.flush()
        lines = logs.log_lines_backfill(db, sid, limit=10)
        assert [line["text"] for line in lines] == ["old session line"]
    finally:
        logs.reset()


# ---------- conda_env argv wrapping (Windows cmd /c) ----------
async def test_pipeline_conda_env_wraps_with_cmd_on_windows():
    import os as _os
    m = ModelConfig("m1", ("m1",), "Chat", 8000, False,
                    {"s": Scheme("s", frozenset({"rtx 4060"}),
                                 Command(exe="lmdeploy", args=("serve", "x"), conda_env="lmdeploy"),
                                 {"rtx 4060": 2048})})
    life, sup, dev, cfg = _make(models=[m])
    await life.ensure_running("m1")
    spawned = sup.spawned[0]
    if _os.name == "nt":
        assert spawned[:3] == ["cmd", "/c", "conda"]
    else:
        assert spawned[:1] == ["conda"]
    assert spawned[-2:] == ["serve", "x"]            # exe args tail
    assert "-n" in spawned and "lmdeploy" in spawned  # conda env passed


# ---------- get_cfg read-through ----------
async def test_lifecycle_reads_fresh_cfg_each_call():
    """get_cfg 返回值变化后,_cfg_model/_runnable/unload_all 反映新模型集(读穿)。"""
    current = {"cfg": _cfg(_model("m1", port=8000))}
    life = Lifecycle(get_cfg=lambda: current["cfg"], supervisor=FakeSupervisor(),
                     devices=FakeDevices(), probes={"Chat": _ok_probe})
    assert "m1" in life._get_cfg().models
    # 模拟 CRUD 加模型 m2 → lifecycle 立即看见(无需重建)
    current["cfg"] = _cfg(_model("m1", port=8000), _model("m2", port=8001))
    assert set(life._get_cfg().models) == {"m1", "m2"}
    # _runnable 走新模型集:m2 可被纳入(虽未 routing)
    state.set_status("m2", ModelStatus.ROUTING, force=True)
    state.record_pid("m2", 42)
    runnable = life._runnable(exclude="m1")
    assert "m2" in runnable


# ---------- Task 7: lifecycle runtime hooks ----------
async def test_runtime_session_recorded_on_start_and_stop(tmp_path):
    from llm_manager.data.persistence import open_db
    db = open_db(tmp_path / "t.db")
    life, sup, dev, cfg = _make(db=db)
    await life.ensure_running("m1")                       # → ROUTING → runtime start
    open_rows = db.conn.execute(
        "SELECT COUNT(*) AS n FROM model_runtime r JOIN models m ON r.model_id=m.id "
        "WHERE m.original_name='m1' AND r.end_time IS NULL").fetchone()
    assert open_rows["n"] == 1
    await life.stop("m1")                                 # → runtime end
    closed = db.conn.execute(
        "SELECT end_time FROM model_runtime r JOIN models m ON r.model_id=m.id "
        "WHERE m.original_name='m1'").fetchone()
    assert closed["end_time"] is not None


async def test_runtime_not_recorded_when_db_absent(tmp_path):
    # default _make() (no db) must not crash and must not record
    life, sup, dev, cfg = _make()
    await life.ensure_running("m1")
    assert state.get_status("m1") == ModelStatus.ROUTING
    await life.stop("m1")
    assert state.get_status("m1") == ModelStatus.STOPPED
    # no assertion crash = pass (db is None path guarded)


async def test_runtime_session_closed_on_crash(tmp_path):
    from llm_manager.data.persistence import open_db
    db = open_db(tmp_path / "t.db")
    life, sup, dev, cfg = _make(db=db)
    await life.ensure_running("m1")                       # ROUTING → open session
    sup.trigger_exit(1000, code=1)                        # external crash → _on_crash
    assert state.get_status("m1") == ModelStatus.FAILED
    rows = db.conn.execute(
        "SELECT end_time FROM model_runtime r JOIN models m ON r.model_id=m.id "
        "WHERE m.original_name='m1'").fetchall()
    assert len(rows) == 1 and rows[0]["end_time"] is not None   # session closed exactly once
    await life.stop("m1")                                 # stop on FAILED → no double-record
    rows2 = db.conn.execute("SELECT COUNT(*) AS n FROM model_runtime").fetchone()
    assert rows2["n"] == 1


async def test_runtime_session_closed_on_reconcile_dead(tmp_path):
    """Fix 2 pin:exit cb 丢失(进程死但 on_exit 未触发)→ _reconcile 关旧会话、重启开新会话。"""
    from llm_manager.data.persistence import open_db
    db = open_db(tmp_path / "t.db")
    life, sup, dev, cfg = _make(db=db)
    await life.ensure_running("m1")                       # ROUTING → session 1 open
    sup.alive_pids.discard(1000)                          # process dead, exit cb never fired
    status = await life.ensure_running("m1")              # reconcile → _runtime_end → restart
    assert status == ModelStatus.ROUTING
    rows = db.conn.execute(
        "SELECT end_time FROM model_runtime r JOIN models m ON r.model_id=m.id "
        "WHERE m.original_name='m1' ORDER BY r.id").fetchall()
    assert len(rows) == 2                                 # old closed + new open
    assert rows[0]["end_time"] is not None
    assert rows[1]["end_time"] is None


# ---------- Task 5: model log sessions (DB-backed) ----------
async def test_model_log_session_open_on_spawn_closed_on_stop(tmp_path):
    """spawn 后:resolve_session 非 None + DB 有进行中会话(行可落库);
    stop 后:end_time 落库 + resolve_session 返回 None。"""
    from llm_manager.data.persistence import open_db
    logs.reset()   # 清残留 pending/sessions(早先失败测试可能遗留内存行)
    db = open_db(tmp_path / "t.db")
    logs.init(db)
    try:
        life, sup, dev, cfg = _make(db=db)
        await life.ensure_running("m1")
        sid = logs.resolve_session("m1")
        assert sid is not None
        rows = logs.log_sessions(db, type_="model", model_name="m1")
        assert len(rows) == 1 and rows[0]["end_time"] is None   # 进行中
        assert rows[0]["alias"] == "m1"                          # alias=aliases[0](served name)
        # spawn 输出 → 该会话的日志行(接线 on_output → capture 按 alias 关联)
        logs.capture("m1", "server listening on :8000", "out")
        await logs.flush()
        lines = logs.log_lines_backfill(db, sid, limit=10)
        assert [ll["text"] for ll in lines] == ["server listening on :8000"]

        await life.stop("m1")
        assert logs.resolve_session("m1") is None
        rows = logs.log_sessions(db, type_="model", model_name="m1")
        assert rows[0]["end_time"] is not None   # stop 收口(旧 bug:end_session 收到字符串静默 no-op)
    finally:
        logs.reset()


async def test_model_log_session_closed_on_crash(tmp_path):
    """进程崩溃(on_exit 回调)→ 会话收口(end_time 落库)。"""
    from llm_manager.data.persistence import open_db
    logs.reset()
    db = open_db(tmp_path / "t.db")
    logs.init(db)
    try:
        life, sup, dev, cfg = _make(db=db)
        await life.ensure_running("m1")
        assert logs.resolve_session("m1") is not None
        sup.trigger_exit(1000, code=1)
        assert logs.resolve_session("m1") is None
        rows = logs.log_sessions(db, type_="model", model_name="m1")
        assert len(rows) == 1 and rows[0]["end_time"] is not None
    finally:
        logs.reset()


async def test_model_log_session_restart_opens_new_session_closes_old(tmp_path):
    """快速 restart:新 spawn 先收口旧会话、再开新会话(防残留,id 各不同)。"""
    from llm_manager.data.persistence import open_db
    logs.reset()
    db = open_db(tmp_path / "t.db")
    logs.init(db)
    try:
        life, sup, dev, cfg = _make(db=db)
        await life.ensure_running("m1")
        sid1 = logs.resolve_session("m1")
        await life.stop("m1")
        await life.ensure_running("m1")
        sid2 = logs.resolve_session("m1")
        assert sid1 != sid2 and sid2 is not None
        rows = logs.log_sessions(db, type_="model", model_name="m1")   # id 降序:最新在前
        assert len(rows) == 2
        assert rows[0]["end_time"] is None       # 新会话进行中
        assert rows[1]["end_time"] is not None   # 旧会话已收口
    finally:
        logs.reset()


async def test_model_log_session_closed_on_probe_failure(tmp_path):
    """probe 失败路径:会话已在 spawn 打开,FAILED 早退不触发 stop/_on_crash → spawn 后必须收口。"""
    from llm_manager.data.persistence import open_db
    logs.reset()
    db = open_db(tmp_path / "t.db")
    logs.init(db)
    try:
        life, sup, dev, cfg = _make(
            db=db, probes={"Chat": lambda *a, **k: ProbeResult(False, "unhealthy")})
        status = await life.ensure_running("m1")
        assert status == ModelStatus.FAILED
        assert logs.resolve_session("m1") is None
        rows = logs.log_sessions(db, type_="model", model_name="m1")
        assert len(rows) == 1 and rows[0]["end_time"] is not None
    finally:
        logs.reset()


async def test_stop_during_spawn_closes_log_session(tmp_path):
    """stop 恰在 spawn await 中到达(会话尚未打开)→ stop 自身 _log_end 收口了个寂寞;
    orphan 分支返回 STOPPED 时必须补收口,否则 running 会话永久泄漏(下次 stop 早退,无人再关)。"""
    from llm_manager.data.persistence import open_db
    logs.reset()
    db = open_db(tmp_path / "t.db")
    logs.init(db)
    try:
        life, sup, dev, cfg = _make(db=db)
        spawn_entered = asyncio.Event()
        orig_spawn = sup.spawn
        async def slow_spawn(cmd, *, shell=False, on_output=None, env=None, cwd=None):
            spawn_entered.set()
            await asyncio.sleep(0.05)   # 给 stop() 在 spawn await 中插入的窗口
            return await orig_spawn(cmd, shell=shell, on_output=on_output)
        sup.spawn = slow_spawn
        task = asyncio.create_task(life.ensure_running("m1"))
        await spawn_entered.wait()                  # 管线已进 spawn await(会话未开)
        await life.stop("m1")                       # stop 先跑:_log_end pops nothing(会话未开)
        status = await task
        assert status == ModelStatus.STOPPED
        assert 1000 in sup.killed                   # orphan 被 kill
        assert logs.resolve_session("m1") is None
        rows = logs.log_sessions(db, type_="model", model_name="m1")
        assert len(rows) == 1 and rows[0]["end_time"] is not None   # 无泄漏:orphan 分支已收口
    finally:
        logs.reset()


async def test_model_log_sessions_tracked_per_alias(tmp_path):
    """多模型并发:各 alias 独立会话,互不干扰(stop 一个不收口另一个)。"""
    from llm_manager.data.persistence import open_db
    logs.reset()
    db = open_db(tmp_path / "t.db")
    logs.init(db)
    try:
        m1 = _model("m1", port=8000)
        m2 = _model("m2", port=8001)
        life, sup, dev, cfg = _make(models=[m1, m2], db=db)
        await life.ensure_running("m1")
        await life.ensure_running("m2")
        assert logs.resolve_session("m1") is not None
        assert logs.resolve_session("m2") is not None
        await life.stop("m1")
        assert logs.resolve_session("m1") is None
        assert logs.resolve_session("m2") is not None     # m2 会话不受影响
        rows1 = logs.log_sessions(db, type_="model", model_name="m1")
        rows2 = logs.log_sessions(db, type_="model", model_name="m2")
        assert rows1[0]["end_time"] is not None
        assert rows2[0]["end_time"] is None
    finally:
        logs.reset()
