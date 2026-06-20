
from starlette.testclient import TestClient

from llm_manager.bootstrap.container import AppContainer

YAML = """
program: {host: "127.0.0.1", port: 9090, data_dir: ./data}
Local-Models:
  Qwen:
    aliases: ["Qwen"]
    mode: "Chat"
    port: 10001
    V100: {required_devices: ["v100"], script_path: "qwen.bat", memory_mb: {v100: 8000}}
"""


def _make_container(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = tmp_path / "config.yaml"
    p.write_text(YAML, encoding="utf-8")
    return AppContainer(p)


def test_health_and_models(tmp_path, monkeypatch):
    c = _make_container(tmp_path, monkeypatch)
    with TestClient(c.app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/v1/models").status_code == 200
        assert client.get("/v1/models").json()["data"][0]["id"] == "Qwen"


def test_proxy_catchall_returns_501(tmp_path, monkeypatch):
    c = _make_container(tmp_path, monkeypatch)
    with TestClient(c.app) as client:
        r = client.post("/v1/chat/completions", json={"model": "Qwen", "messages": []})
        assert r.status_code == 501


def test_unknown_get_is_not_spa(tmp_path, monkeypatch):
    c = _make_container(tmp_path, monkeypatch)
    with TestClient(c.app) as client:
        # No SPA catch-all: an unknown GET must NOT return index.html.
        # The proxy catch-all is non-GET only, so this GET yields 405.
        assert client.get("/some/unknown/path").status_code == 405
