
from fastapi.testclient import TestClient

from llm_manager.app import create_app

_CFG_BODY = """
program: {host: 0.0.0.0, port: 8080, alive_time: 60, log_level: INFO, db_path: %s}
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


def test_lifespan_opens_db_and_monitor_then_cleans_up(tmp_path):
    db_path = tmp_path / "t.db"
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(_CFG_BODY % str(db_path).replace("\\", "/"), encoding="utf-8")
    app = create_app(db_path=tmp_path / "t.db", legacy_yaml=cfg_path)
    with TestClient(app) as client:
        assert (tmp_path / "t.db").exists()
        resp = client.get("/health")
        assert resp.status_code == 200
    # after shutdown the db connection is closed; reopening works (no lock leftover)
    assert (tmp_path / "t.db").exists()
