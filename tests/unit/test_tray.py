"""SystemTray host unit tests.

The pystray Icon/run() loop needs a real display and is not exercised here; only
the import-guard + action methods are tested. The async-marshal seam is
``_run_coro_threadsafe`` (overridden on the instance to capture + run the coro
on the test loop); the exit seam is the loop/server pair.
"""

import asyncio

import pytest

from llm_manager.config import AppConfig, ProgramConfig
from llm_manager import tray


def _cfg(claude_configs=None):
    return AppConfig(
        program=ProgramConfig(
            host="0.0.0.0",
            port=8080,
            alive_time=60,
            log_level="INFO",
            claude_settings_path="s.json",
        ),
        models={},
        wol=None,
        claude_configs=claude_configs or {},
    )


class _FakeLife:
    def __init__(self):
        self.unload_called = False

    async def unload_all(self):
        self.unload_called = True
        return []


class _FakeServer:
    def __init__(self):
        self.should_exit = False


class _FakeLoop:
    def __init__(self):
        self.scheduled: list = []
        self._closed = False

    def call_soon_threadsafe(self, cb, *args):
        self.scheduled.append((cb, args))

    def is_closed(self):
        return self._closed


def _make_tray(**over):
    base = {
        "lifecycle": _FakeLife(),
        "get_cfg": lambda: _cfg(),
        "monitor": object(),
        "loop": _FakeLoop(),
        "server": _FakeServer(),
        "settings_path": "s.json",
        "startup_timeout": 60.0,
        "auto_start_margin": 30.0,
    }
    base.update(over)
    return tray.SystemTray(**base)


# ---------- availability ----------
def test_is_tray_available_false_when_pystray_missing(monkeypatch):
    monkeypatch.setattr(tray, "_PYSTRAY_AVAILABLE", False)
    assert tray.is_tray_available() is False


def test_is_tray_available_true_when_present(monkeypatch):
    monkeypatch.setattr(tray, "_PYSTRAY_AVAILABLE", True)
    assert tray.is_tray_available() is True


def test_is_headless_display_posix_without_display(monkeypatch):
    monkeypatch.setattr(tray.os, "name", "posix")
    monkeypatch.delenv("DISPLAY", raising=False)
    assert tray._is_headless_display() is True
    monkeypatch.setenv("DISPLAY", ":0")
    assert tray._is_headless_display() is False


# ---------- Claude preset ----------
def test_apply_claude_delegates_to_apply_preset(monkeypatch, tmp_path):
    settings = tmp_path / "settings.json"
    tray_host = _make_tray(
        get_cfg=lambda: _cfg(claude_configs={"Local": {"ANTHROPIC_BASE_URL": "http://x"}}),
        settings_path=settings,
    )
    called = []
    monkeypatch.setattr(
        tray.claude, "apply_preset", lambda path, preset: called.append((path, preset))
    )
    tray_host.apply_claude("Local")
    assert called == [(settings, {"ANTHROPIC_BASE_URL": "http://x"})]


def test_apply_claude_unknown_preset_noop(monkeypatch, tmp_path):
    tray_host = _make_tray(settings_path=tmp_path / "s.json")
    called = []
    monkeypatch.setattr(tray.claude, "apply_preset", lambda *a: called.append(a))
    tray_host.apply_claude("Nonexistent")
    assert called == []


def test_apply_claude_empty_settings_path_is_noop(monkeypatch):
    # claude_settings_path 可空(托盘启动门槛已移除):空路径不得写库(Path("") 会落到 cwd)
    tray_host = _make_tray(settings_path="")
    called = []
    monkeypatch.setattr(tray.claude, "apply_preset", lambda *a: called.append(a))
    tray_host.apply_claude("Local")
    assert called == []


def test_apply_claude_none_settings_path_is_noop(monkeypatch):
    # 空库首次启动:claude_settings_path 为 None——Path(None) 曾致启动崩溃(ddfffe1 修复的回归)
    tray_host = _make_tray(settings_path=None)
    called = []
    monkeypatch.setattr(tray.claude, "apply_preset", lambda *a: called.append(a))
    tray_host.apply_claude("Local")
    assert called == []


# ---------- async marshal ----------
async def test_unload_all_marshals_lifecycle_unload_all():
    life = _FakeLife()
    tray_host = _make_tray(lifecycle=life)
    captured = []

    def fake_schedule(coro):
        task = asyncio.ensure_future(coro)
        captured.append(
            task
        )  # 捕获 task 而非 coro:ensure_future 已驱动 coro,再 await 原始 coro 会报 "cannot reuse already awaited coroutine"
        return task

    tray_host._run_coro_threadsafe = fake_schedule
    tray_host.unload_all()
    assert len(captured) == 1
    await captured[0]
    assert life.unload_called is True


async def test_restart_auto_start_unloads_then_autostarts(monkeypatch):
    life = _FakeLife()
    tray_host = _make_tray(lifecycle=life, get_cfg=lambda: _cfg())  # models empty → auto_models []
    captured = []

    def fake_schedule(coro):
        task = asyncio.ensure_future(coro)
        captured.append(
            task
        )  # 捕获 task 而非 coro:ensure_future 已驱动 coro,再 await 原始 coro 会报 "cannot reuse already awaited coroutine"
        return task

    tray_host._run_coro_threadsafe = fake_schedule
    autostart_calls = []

    async def fake_auto_start(lifecycle, models, cfg, monitor, *, timeout, stop_event):
        autostart_calls.append((list(models), timeout))

    monkeypatch.setattr(tray.background, "auto_start", fake_auto_start)
    tray_host.restart_auto_start()
    assert len(captured) == 1
    await captured[0]
    assert life.unload_called is True
    assert autostart_calls == [([], 90.0)]  # startup_timeout 60 + margin 30


# ---------- exit ----------
def test_exit_app_sets_server_should_exit():
    server = _FakeServer()
    loop = _FakeLoop()
    tray_host = _make_tray(loop=loop, server=server)
    tray_host.exit_app()
    assert len(loop.scheduled) == 1
    cb, args = loop.scheduled[0]
    cb(*args)  # execute setattr(server, "should_exit", True)
    assert server.should_exit is True


def test_run_coro_threadsafe_closes_coro_when_loop_closed():
    tray_host = _make_tray()
    tray_host._loop._closed = True

    async def never_run():
        pytest.fail("coroutine should not run on a closed loop")

    tray_host._run_coro_threadsafe(never_run())  # must not raise; coro closed cleanly


def test_send_wol_uses_fresh_wol_from_store(monkeypatch, tmp_path):
    from llm_manager.config import AppConfig, ProgramConfig, WakeOnLanConfig

    current = {"wol": WakeOnLanConfig("10.0.0.255", "aa:bb:cc:dd:ee:ff")}

    def get_cfg():
        return AppConfig(
            program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
            models={},
            wol=current["wol"],
            claude_configs={},
        )

    tray_host = _make_tray(get_cfg=get_cfg)
    sent = []
    monkeypatch.setattr(tray.wol, "send_wol", lambda mac, bcast: sent.append((mac, bcast)))
    tray_host.send_wol()
    assert sent == [("aa:bb:cc:dd:ee:ff", "10.0.0.255")]
    # 模拟「写回 wol」→ tray 下次动作用新值
    current["wol"] = WakeOnLanConfig("172.16.0.255", "11:22:33:44:55:66")
    tray_host.send_wol()
    assert sent[-1] == ("11:22:33:44:55:66", "172.16.0.255")
