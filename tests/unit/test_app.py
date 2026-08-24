import sys
import time

import pytest
from fastapi.testclient import TestClient
from helpers import cfg as build_cfg
from helpers import model as build_model
from helpers import scheme as build_scheme

from llm_manager import state
from llm_manager.app import create_app, create_dev_app
from llm_manager.data.config_store import write_appconfig
from llm_manager.data.persistence import open_db
from llm_manager.state import ModelStatus


def _seed(db_path, *, models=None, program=None):
    """write_appconfig 预置 DB(启动期 initialize 检测到已 initialized → 跳过 seed)。"""
    db = open_db(db_path)
    try:
        write_appconfig(db, build_cfg(models=models, program=program))
    finally:
        db.conn.close()


_M1 = lambda: {
    "m1": build_model(
        ("m1",),
        8000,
        auto_start=True,
        schemes={"RTX4060": build_scheme(devices=("rtx 4060",), memory_mb={"rtx 4060": 2048})},
    )
}


def test_lifespan_starts_and_stops_background(tmp_path, monkeypatch):
    # enumerate_lhm_gpus 内部职责;mock devices.common.is_lhm_available=False → 等效隔离 LHM 慢调用,聚焦 lifespan+background。
    monkeypatch.setattr("llm_manager.devices.common.is_lhm_available", lambda: False)
    # CI(runner)无 NVIDIA 卡/驱动:build_adapters 枚举不到 rtx 4060 → select_adaptive 静默跳过
    # → 模型停在 STOPPED(Failed 断言永等不到)。钉住适配器枚举,与 Windows 本机行为对齐。
    from llm_manager.devices import DeviceInfo

    monkeypatch.setattr(
        "llm_manager.devices.nvidia.NvidiaAdapter.enumerate",
        lambda self: [
            DeviceInfo("NVIDIA GeForce RTX 4060", "GPU", "VRAM", 8192, 4096, 0, 0.0, 45.0)
        ],
    )
    # 探针秒失败(跳过真实 60s 重试循环):仍证明 auto_start 后台真起 + 失败容错(不抛)+ 不阻塞 /health。
    # 测试的真实契约是「后台任务起 + 失败路径走通 + /health 不阻塞」,「重试 60s」只是 startup_timeout 的副作用。
    from llm_manager.runtime.probes import ProbeResult, probe_registry

    monkeypatch.setitem(
        probe_registry,
        "Chat",
        lambda alias, port, start_time=None, timeout=300: ProbeResult(False, "test fast-fail"),
    )
    _seed(tmp_path / "t.db", models=_M1())
    app = create_app(db_path=tmp_path / "t.db")
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200  # fire-and-forget:就绪不等 auto_start
        deadline = time.monotonic() + 10  # 秒失败探针:m1 应 <2s FAILED(留余量)
        while time.monotonic() < deadline and state.get_status("m1") != ModelStatus.FAILED:
            time.sleep(0.05)
        assert state.get_status("m1") == ModelStatus.FAILED
    # with 退出 → lifespan finally:stop_event.set() + unload_all + cancel+gather,干净关闭无异常


def test_create_app_preserves_db_state_across_boot(tmp_path):
    """同一 DB 反复 create_app:已 initialized → 跳过 seed,保留既有模型。"""
    _seed(tmp_path / "t.db", models=_M1())
    app1 = create_app(db_path=tmp_path / "t.db")
    assert "m1" in app1.state.config_store.snapshot().models
    app2 = create_app(db_path=tmp_path / "t.db")
    assert "m1" in app2.state.config_store.snapshot().models  # 二次启动保留 DB 状态


def test_create_app_closes_db_on_bootstrap_error(tmp_path):
    db_path = tmp_path / "t.db"
    # 无效配置(schema 合法但 validate 失败:无 device scheme)→ create_app 抛 ValueError
    _seed(db_path, models={"A": build_model(("x",), 1)})
    with pytest.raises(ValueError):
        create_app(db_path=db_path)
    # db.conn 应已关闭:可重新打开(Windows 上未关会锁文件)
    open_db(db_path).conn.execute("SELECT 1").fetchone()  # 不抛


def test_crud_then_catalog_reflects_without_restart(tmp_path, monkeypatch):
    """核心契约:POST /api/config/models 后,不重启即见 /v1/models + /api/config/models(读穿)。"""
    monkeypatch.setattr(
        "llm_manager.devices.common.is_lhm_available", lambda: False
    )  # 隔离 LHM 慢枚举
    _seed(
        tmp_path / "t.db",
        models={
            "A": build_model(
                ("a",), 9001, schemes={"S": build_scheme(devices=("gpu",), memory_mb={"gpu": 1})}
            )
        },
    )
    app = create_app(db_path=tmp_path / "t.db")
    with TestClient(app) as c:
        # 初始:A 在册
        assert "a" in {m["id"] for m in c.get("/v1/models").json()["data"]}
        # CRUD 加 B
        r = c.post(
            "/api/config/models",
            json={
                "name": "B",
                "mode": "Chat",
                "port": 9002,
                "auto_start": False,
                "aliases": ["b"],
                "schemes": [
                    {
                        "config_source": "S",
                        "required_devices": ["gpu"],
                        "command": {"exe": "b.bat"},
                        "memory_mb": {"gpu": 1},
                    }
                ],
            },
        )
        assert r.status_code == 201
        # 不重启即见 B(读穿:/v1/models 与 /api/config/models 都走 config_store.snapshot)
        v1 = {m["id"] for m in c.get("/v1/models").json()["data"]}
        api = {m["name"] for m in c.get("/api/config/models").json()}
        assert "b" in v1 and "B" in api  # v1 用 alias "b";config 列表用 name "B"
        # CRUD 删 A → 反映
        c.delete("/api/config/models/A")
        v1b = {m["id"] for m in c.get("/v1/models").json()["data"]}
        assert "a" not in v1b and "b" in v1b


def test_log_level_from_config_applied(tmp_path, monkeypatch):
    """cfg.program.log_level 真正传入 setup_logging(此前硬编码 INFO 从未生效)。
    只捕获调用参数,不触发真实 logging 副作用(conftest _isolate_logging 隔离)。"""
    monkeypatch.setattr("llm_manager.devices.common.is_lhm_available", lambda: False)
    levels: list[str] = []
    monkeypatch.setattr(
        "llm_manager.app.setup_logging", lambda level="INFO", **kw: levels.append(level)
    )
    _seed(tmp_path / "t.db", models=_M1(), program={"log_level": "DEBUG"})
    create_app(db_path=tmp_path / "t.db")
    assert levels == ["DEBUG"]


def test_create_dev_app_leaves_no_fake_server(tmp_path, monkeypatch):
    monkeypatch.setenv("LLM_MANAGER_DB_PATH", str(tmp_path / "t.db"))
    monkeypatch.chdir(tmp_path)
    app = create_dev_app()
    assert getattr(app.state, "uvicorn_server", None) is None


def test_exit_code_for_returns_sentinel_only_when_requested():
    from llm_manager.runner import RESTART_EXIT_CODE, exit_code_for

    assert exit_code_for(False) == 0
    assert exit_code_for(True) == RESTART_EXIT_CODE == 81


# ---------- parent 监督器辅助(Task 1) ----------


def test_should_respawn_only_on_restart_sentinel():
    from llm_manager.runner import RESTART_EXIT_CODE, _should_respawn

    assert _should_respawn(RESTART_EXIT_CODE) is True
    assert _should_respawn(0) is False
    assert _should_respawn(1) is False
    assert _should_respawn(None) is False
    assert _should_respawn(-9) is False


def test_worker_command_contains_executable_and_flag():
    from llm_manager.runner import _WORKER_FLAG, _worker_command

    cmd = _worker_command()
    assert cmd[0] == sys.executable
    assert "-m" in cmd and "llm_manager" in cmd
    assert _WORKER_FLAG in cmd


def test_spawn_kwargs_windows_uses_process_group(monkeypatch):
    import subprocess

    import llm_manager.runner as appmod

    # CREATE_NEW_PROCESS_GROUP 仅在 Windows 子进程模块中定义;POSIX 上测试
    # monkeypatch os.name 骗过分支后引用该常量会 AttributeError —— 注入假常量。
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(appmod.os, "name", "nt")
    kw = appmod._spawn_kwargs()
    assert kw["creationflags"] == subprocess.CREATE_NEW_PROCESS_GROUP


def test_spawn_kwargs_posix_uses_new_session(monkeypatch):
    import llm_manager.runner as appmod

    monkeypatch.setattr(appmod.os, "name", "posix")
    kw = appmod._spawn_kwargs()
    assert kw["start_new_session"] is True
    assert "creationflags" not in kw


# ---------- 信号转发辅助(Task 2) ----------


def test_forwardable_signals_per_os(monkeypatch):
    import signal as _sig

    import llm_manager.runner as appmod

    monkeypatch.setattr(appmod.os, "name", "nt")
    assert appmod._forwardable_signals() == [_sig.SIGINT]
    monkeypatch.setattr(appmod.os, "name", "posix")
    sigs = appmod._forwardable_signals()
    assert _sig.SIGINT in sigs and _sig.SIGTERM in sigs


def test_send_shutdown_windows_sends_ctrl_break(monkeypatch):
    import signal as _sig

    import llm_manager.runner as appmod

    # CTRL_BREAK_EVENT 仅在 Windows 信号扩展中导出,POSIX 上引用即 AttributeError。
    monkeypatch.setattr(_sig, "CTRL_BREAK_EVENT", 1, raising=False)
    monkeypatch.setattr(appmod.os, "name", "nt")
    sent = {}

    class FakeProc:
        pid = 123

        def send_signal(self, s):
            sent["sig"] = s

    appmod._send_shutdown(FakeProc())
    assert sent["sig"] == _sig.CTRL_BREAK_EVENT


def test_send_shutdown_posix_killpg(monkeypatch):
    import signal as _sig

    import llm_manager.runner as appmod

    monkeypatch.setattr(appmod.os, "name", "posix")
    killed = {}
    # getpgid/killpg 在 Windows 不存在,raising=False 允许注入以测 POSIX 分支
    monkeypatch.setattr(appmod.os, "getpgid", lambda pid: 999, raising=False)
    monkeypatch.setattr(
        appmod.os,
        "killpg",
        lambda pgid, sig: killed.__setitem__("args", (pgid, sig)),
        raising=False,
    )

    class FakeProc:
        pid = 123

    appmod._send_shutdown(FakeProc())
    assert killed["args"] == (999, _sig.SIGTERM)


def test_send_shutdown_missing_process_is_silent(monkeypatch):
    import llm_manager.runner as appmod

    monkeypatch.setattr(appmod.os, "name", "posix")

    def _raise_ple(pid):
        raise ProcessLookupError()

    # getpgid 抛 ProcessLookupError → 应被 _send_shutdown 吞掉。
    # killpg 仅作占位使其属性可解析(实际不会被调用:getpgid 先抛)。
    monkeypatch.setattr(appmod.os, "getpgid", _raise_ple, raising=False)
    monkeypatch.setattr(appmod.os, "killpg", lambda *a: None, raising=False)

    class FakeProc:
        pid = 123

    appmod._send_shutdown(FakeProc())  # 不抛


def test_force_kill_when_still_running():
    import llm_manager.runner as appmod

    killed = {}

    class FakeProc:
        def poll(self):
            return None  # 仍运行

        def kill(self):
            killed["killed"] = True

    appmod._force_kill(FakeProc())
    assert killed.get("killed") is True


def test_force_kill_noop_when_exited():
    import llm_manager.runner as appmod

    class FakeProc:
        def poll(self):
            return 0  # 已退出

        def kill(self):
            raise AssertionError("不应 kill 已退出的进程")

    appmod._force_kill(FakeProc())


# ---------- 入口分派(Task 3) ----------


def test_main_dispatches_to_worker_when_flag(monkeypatch):
    import llm_manager.runner as appmod

    called = {}
    monkeypatch.setattr(appmod, "_run_worker", lambda: called.__setitem__("w", True))
    monkeypatch.setattr(appmod, "_run_parent", lambda: called.__setitem__("p", True))
    monkeypatch.setattr(appmod.sys, "argv", ["llm_manager", "--worker"])
    appmod.main()
    assert called == {"w": True}


def test_main_dispatches_to_parent_by_default(monkeypatch):
    import llm_manager.runner as appmod

    called = {}
    monkeypatch.setattr(appmod, "_run_worker", lambda: called.__setitem__("w", True))
    monkeypatch.setattr(appmod, "_run_parent", lambda: called.__setitem__("p", True))
    monkeypatch.setattr(appmod.sys, "argv", ["llm_manager"])
    appmod.main()
    assert called == {"p": True}
