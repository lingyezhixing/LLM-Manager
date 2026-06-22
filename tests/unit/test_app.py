import time

from fastapi.testclient import TestClient

from llm_manager import state
from llm_manager.app import create_app
from llm_manager.state import ModelStatus

_CFG_BODY = """
program: {host: 127.0.0.1, port: 8080, alive_time: 60, log_level: INFO}
Local-Models:
  m1:
    aliases: ["m1"]
    mode: Chat
    port: 8000
    auto_start: true
    RTX4060:
      required_devices: ["rtx 4060"]
      script_path: "nonexistent.cmd"
      memory_mb: {"rtx 4060": 2048}
"""


def test_lifespan_starts_and_stops_background(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(_CFG_BODY, encoding="utf-8")
    app = create_app(cfg_path)
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200   # fire-and-forget:就绪不等 auto_start
        # 轮询:auto_start 后台真跑(spawn nonexistent.cmd → 无 scheme 或 probe refused → FAILED),
        # 证明 create_task 真起 + _one try/except 容错(不抛)+ 不阻塞 /health
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline and state.get_status("m1") != ModelStatus.FAILED:
            time.sleep(0.1)
        assert state.get_status("m1") == ModelStatus.FAILED
    # with 退出 → lifespan finally:stop_event.set() + unload_all + cancel+gather,干净关闭无异常


def test_app_registers_780m_when_lhm_available(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(_CFG_BODY, encoding="utf-8")
    monkeypatch.setattr("llm_manager.app.is_lhm_available", lambda: True)
    app = create_app(cfg_path)
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert "780m" in app.state.monitor._devices


def test_app_skips_780m_when_lhm_unavailable(tmp_path, monkeypatch):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(_CFG_BODY, encoding="utf-8")
    monkeypatch.setattr("llm_manager.app.is_lhm_available", lambda: False)
    app = create_app(cfg_path)
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200
        assert "780m" not in app.state.monitor._devices
