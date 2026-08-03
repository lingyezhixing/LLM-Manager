
import time

from fastapi.testclient import TestClient

from llm_manager.app import create_app
from llm_manager.data import logs
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
        rows = logs.log_sessions(app.state.db, type_="system")
        assert rows[0]["end_time"] is None            # 进行中
    # shutdown 后连接已关:重开检查 end_time 落库
    db2 = open_db(tmp_path / "t.db")
    try:
        rows = logs.log_sessions(db2, type_="system")
        assert rows[0]["end_time"] is not None        # shutdown 收口
    finally:
        db2.conn.close()
    assert logs.current_system_session_id() is None   # 内存登记已清除


def test_lifespan_new_system_session_after_crash(tmp_path):
    """崩溃残留会话(SQL 直插模拟)在新进程 live_session_ids 之外 → status=ended,无需启动收口;
    同时开新 system 会话,shutdown 时被正常关闭。注:真实崩溃残留经心跳维持 end_time(≈死亡时刻),
    本测用 SQL 直插仅验「运行中」状态已与 end_time 解耦。"""
    db_path = tmp_path / "t.db"
    db = open_db(db_path)
    try:
        resid = logs.log_start_session(db, "system", None, None, time.time() - 100)
    finally:
        db.conn.close()
    app = create_app(db_path=db_path)
    with TestClient(app):
        rows = logs.log_sessions(app.state.db, type_="system")
        by_id = {r["id"]: r for r in rows}
        assert by_id[resid]["status"] == "ended"        # 不在 live_session_ids → ended(无需收口)
        current = logs.current_system_session_id()
        assert current is not None and current != resid   # 新会话已开
        assert by_id[current]["status"] == "running"
    # shutdown 关闭新会话(current 的 end_time 被写;resid 是 SQL 直插假残留,end_time 未经心跳)
    db2 = open_db(db_path)
    try:
        rows = {r["id"]: r for r in logs.log_sessions(db2, type_="system")}
        assert rows[current]["end_time"] is not None    # 新会话 shutdown 时关闭
    finally:
        db2.conn.close()
