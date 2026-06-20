
from starlette.testclient import TestClient

from llm_manager.app import build_app

YAML = """
program: {host: "127.0.0.1", port: 9090, data_dir: ./data}
Local-Models:
  Qwen:
    aliases: ["Qwen"]
    mode: "Chat"
    port: 10001
    V100: {required_devices: ["v100"], script_path: "qwen.bat", memory_mb: {v100: 8000}}
"""


def test_build_app_boots_and_serves(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "config.yaml"
    cfg.write_text(YAML, encoding="utf-8")
    app = build_app(cfg)
    with TestClient(app) as client:
        assert client.get("/health").status_code == 200
        assert client.get("/v1/models").status_code == 200
        assert client.post("/v1/chat/completions", json={}).status_code == 501
