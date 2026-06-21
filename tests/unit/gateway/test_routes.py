from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from llm_manager import config
from llm_manager.gateway.routes import register_routes

_CFG_BODY = """
program: {host: 0.0.0.0, port: 8080, alive_time: 60, log_level: INFO}
Local-Models:
  Qwen3-4B:
    aliases: ["Qwen3-4B"]
    mode: Chat
    port: 10001
    RTX4060:
      required_devices: ["rtx 4060"]
      script_path: "q.bat"
      memory_mb: {"rtx 4060": 5120}
"""


def _cfg(tmp_path: Path) -> config.AppConfig:
    p = tmp_path / "config.yaml"
    p.write_text(_CFG_BODY, encoding="utf-8")
    return config.load(p)


def test_health_returns_200(tmp_path):
    app = FastAPI()
    register_routes(app, _cfg(tmp_path))
    with TestClient(app) as client:
        resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_v1_models_returns_catalog(tmp_path):
    app = FastAPI()
    register_routes(app, _cfg(tmp_path))
    with TestClient(app) as client:
        resp = client.get("/v1/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["object"] == "list"
    ids = {m["id"] for m in body["data"]}
    assert "Qwen3-4B" in ids


def test_options_preflash_returns_204_with_cors(tmp_path):
    app = FastAPI()
    register_routes(app, _cfg(tmp_path))
    with TestClient(app) as client:
        resp = client.options("/v1/chat/completions")
    assert resp.status_code == 204
    assert resp.headers.get("access-control-allow-origin") == "*"


def test_non_get_catchall_hits_proxy_stub_501(tmp_path):
    app = FastAPI()
    register_routes(app, _cfg(tmp_path))
    with TestClient(app) as client:
        resp = client.post("/v1/chat/completions", json={"model": "x"})
    assert resp.status_code == 501
