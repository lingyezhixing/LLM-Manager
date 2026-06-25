import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_manager import config, state
from llm_manager.data.persistence import open_db
from llm_manager.gateway.routes import register_routes
from llm_manager.state import ModelStatus

_CFG = """
program: {host: 0.0.0.0, port: 8080, alive_time: 60, log_level: INFO}
Local-Models:
  internal-qwen-key:                   # primary_name(内部键,不应外露)
    aliases: ["qwen2.5-32b"]           # aliases[0]=对外身份
    mode: Chat
    port: 8001
    RTX4060:
      required_devices: ["rtx 4060"]
      script_path: "q.bat"
      memory_mb: {"rtx 4060": 2048}
"""


class _FakeLife:
    async def ensure_running(self, alias, *, inc_pending=False):
        return ModelStatus.ROUTING


def _app(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(_CFG, encoding="utf-8")
    cfg = config.load(p)
    app = FastAPI()
    register_routes(app, _FakeLife(), cfg, open_db(Path(":memory:")), {})
    return app


def test_api_models_maps_state_and_config(tmp_path):
    state._reset()
    app = _app(tmp_path)
    state.set_status("internal-qwen-key", ModelStatus.ROUTING, force=True)
    state.record_pid("internal-qwen-key", 12840)
    state.inc_pending("internal-qwen-key")
    state._set_last_access("internal-qwen-key", time.monotonic() - 12.0)
    with TestClient(app) as c:
        r = c.get("/api/models")
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 1
    m = data[0]
    assert m["alias"] == "qwen2.5-32b"                 # aliases[0], not internal key
    assert m["status"] == "routing"
    assert m["pid"] == 12840
    assert m["pending"] == 1
    assert m["mode"] == "Chat" and m["port"] == 8001 and m["auto_start"] is False
    assert m["idle_seconds"] is not None and 10.0 <= m["idle_seconds"] <= 15.0
    assert m["failure_reason"] is None
    state._reset()


def test_api_models_idle_null_when_never_accessed(tmp_path):
    state._reset()
    app = _app(tmp_path)   # no last_access set → default 0.0 → idle_seconds None
    with TestClient(app) as c:
        r = c.get("/api/models")
    m = r.json()["data"][0]
    assert m["idle_seconds"] is None
    assert m["status"] == "stopped"   # default state
    state._reset()
