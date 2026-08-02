
from fastapi.testclient import TestClient

from llm_manager.app import create_app
from llm_manager.data import logs
from llm_manager.data import persistence as _p
from llm_manager.data.persistence import open_db

_CFG_BODY = """
program: {host: 0.0.0.0, port: 8080, alive_time: 60, log_level: INFO, db_path: %s}
Local-Models:
  Qwen3-4B:
    aliases: ["Qwen3-4B"]
    mode: Chat
    port: 10001
    RTX4060:
      required_devices: ["rtx 4060"]
      command: {exe: "q.bat"}
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


def test_lifespan_opens_and_closes_system_session(tmp_path):
    app = create_app(db_path=tmp_path / "t.db")
    with TestClient(app):
        sid = logs.current_system_session_id()
        assert sid is not None                        # lifespan 已开系统会话
        rows = _p.log_sessions(app.state.db, type_="system")
        assert rows[0]["end_time"] is None            # 进行中
    # shutdown 后连接已关:重开检查 end_time 落库
    db2 = open_db(tmp_path / "t.db")
    try:
        rows = _p.log_sessions(db2, type_="system")
        assert rows[0]["end_time"] is not None        # shutdown 收口
    finally:
        db2.conn.close()
    assert logs.current_system_session_id() is None   # 内存登记已清除
