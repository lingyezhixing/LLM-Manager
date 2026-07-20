import asyncio
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_manager import config, state
from llm_manager.data.config_store import ConfigStore, write_appconfig
from llm_manager.data.persistence import open_db
from llm_manager.gateway.api.models import _models_stream, build_models_response
from llm_manager.gateway.routes import register_routes
from llm_manager.realtime import ModelFeed
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
      command: {exe: "q.bat"}
      memory_mb: {"rtx 4060": 2048}
"""


class _FakeLife:
    def __init__(self):
        self.started: list[str] = []
        self.stopped: list[str] = []

    async def ensure_running(self, alias, *, inc_pending=False):
        self.started.append(alias)
        return ModelStatus.ROUTING

    async def stop(self, alias):
        self.stopped.append(alias)
        return state.get_status(alias)


def _cfg(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(_CFG, encoding="utf-8")
    return config.load(p)


def _app(tmp_path, life=None):
    life = _FakeLife() if life is None else life
    db = open_db(Path(":memory:"))
    cfg = _cfg(tmp_path)
    write_appconfig(db, cfg)
    store = ConfigStore(db)
    app = FastAPI()
    register_routes(app, life, db, {})
    app.state.config_store = store
    app.state.db = db
    return app


def test_api_models_maps_state_and_config(tmp_path):
    state._reset()
    app = _app(tmp_path)
    state.set_status("internal-qwen-key", ModelStatus.ROUTING, force=True)
    state.record_pid("internal-qwen-key", 12840)
    state.inc_pending("internal-qwen-key")
    with TestClient(app) as c:
        r = c.get("/api/models")
    assert r.status_code == 200
    m = r.json()["data"][0]
    assert m["alias"] == "qwen2.5-32b"                 # aliases[0], not internal key
    assert m["status"] == "routing"
    assert m["pid"] == 12840
    assert m["pending"] == 1
    assert m["mode"] == "Chat" and m["port"] == 8001 and m["auto_start"] is False
    assert m["started_at"] is not None and m["started_at"] > 0   # routing → uptime start
    assert m["last_access"] > 0                                   # routing touched activity
    assert m["failure_reason"] is None
    state._reset()


def test_api_models_defaults_when_never_accessed(tmp_path):
    state._reset()
    app = _app(tmp_path)
    with TestClient(app) as c:
        r = c.get("/api/models")
    m = r.json()["data"][0]
    assert m["started_at"] is None   # not routing
    assert m["last_access"] == 0.0   # never accessed
    assert m["status"] == "stopped"
    state._reset()


async def test_models_stream_yields_initial_then_on_change(tmp_path):
    """Drive the SSE generator directly (TestClient hangs on infinite streams)."""
    state._reset()
    cfg = _cfg(tmp_path)
    feed = ModelFeed(lambda: build_models_response(cfg), interval=0.01)
    state.set_status("internal-qwen-key", ModelStatus.ROUTING, force=True)

    gen = _models_stream(feed)
    first = await gen.__anext__()
    assert first.startswith("data:")
    assert "routing" in first

    state.set_status("internal-qwen-key", ModelStatus.STOPPED, force=True)   # change → push
    second = await asyncio.wait_for(gen.__anext__(), timeout=2)
    assert "stopped" in second

    await gen.aclose()   # finally → unsubscribe → loop stops
    assert feed.subscriber_count == 0
    state._reset()


def test_start_unknown_alias_404(tmp_path):
    state._reset()
    app = _app(tmp_path)
    with TestClient(app) as c:
        r = c.post("/api/models/nope/start")
    assert r.status_code == 404
    state._reset()


def test_start_when_routing_409(tmp_path):
    state._reset()
    app = _app(tmp_path)
    state.set_status("internal-qwen-key", ModelStatus.ROUTING, force=True)   # keyed by primary_name
    with TestClient(app) as c:
        r = c.post("/api/models/qwen2.5-32b/start")                          # URL uses alias
    assert r.status_code == 409
    state._reset()


def test_start_accepted_202_and_fires_ensure_running(tmp_path):
    state._reset()
    life = _FakeLife()
    app = _app(tmp_path, life)
    with TestClient(app) as c:
        r = c.post("/api/models/qwen2.5-32b/start")
        assert r.status_code == 202
        for _ in range(50):                  # 让后台 create_task 在 portal loop 上跑完
            if life.started:
                break
            time.sleep(0.02)
    assert life.started == ["internal-qwen-key"]     # ensure_running 收到 primary_name
    state._reset()


def test_stop_accepted_202_and_fires_stop(tmp_path):
    state._reset()
    life = _FakeLife()
    app = _app(tmp_path, life)
    with TestClient(app) as c:
        r = c.post("/api/models/qwen2.5-32b/stop")
        assert r.status_code == 202
        for _ in range(50):
            if life.stopped:
                break
            time.sleep(0.02)
    assert life.stopped == ["internal-qwen-key"]
    state._reset()


def test_api_models_reflects_store_reload(tmp_path):
    """读穿:store.reload() 后 /api/models 反映新模型,无需重启/重注册。"""
    state._reset()
    app = _app(tmp_path)
    with TestClient(app) as c:
        from dataclasses import replace
        m2 = config.ModelConfig("m2-key", ("m2-served",), "Chat", 8002,
                                schemes={"s": config.Scheme("s", frozenset({"rtx 4060"}),
                                                            config.Command(exe="q.bat"), {})})
        cur = app.state.config_store.snapshot()
        write_appconfig(app.state.db, replace(cur, models={**cur.models, "m2-key": m2}))
        app.state.config_store.reload()
        r = c.get("/api/models")
    aliases = {m["alias"] for m in r.json()["data"]}
    assert "qwen2.5-32b" in aliases and "m2-served" in aliases
    state._reset()
