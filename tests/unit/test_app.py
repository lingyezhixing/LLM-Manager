import time

import pytest
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
      command: {exe: "nonexistent.cmd"}
      memory_mb: {"rtx 4060": 2048}
"""


def test_lifespan_starts_and_stops_background(tmp_path, monkeypatch):
    # enumerate_lhm_gpus 内部职责,app.py 不再注册 780m;但 lifespan refresh 仍调真 LHM(慢 + 硬件依赖,
    # 经 _lhm_computer),干扰本测 m1 时序断言。mock devices.is_lhm_available=False → _lhm_computer 返 None,
    # 等效隔离 LHM 慢调用,聚焦 lifespan+background。
    monkeypatch.setattr("llm_manager.devices.is_lhm_available", lambda: False)
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(_CFG_BODY, encoding="utf-8")
    app = create_app(db_path=tmp_path / "t.db", legacy_yaml=cfg_path)
    with TestClient(app) as c:
        assert c.get("/health").status_code == 200   # fire-and-forget:就绪不等 auto_start
        # 轮询:auto_start 后台真跑(spawn nonexistent.cmd → 不起服务 → probe 重试 startup_timeout=60s → FAILED)。
        # deadline 须 > startup_timeout(60s):装 monitoring 后 probe/on_exit 时序使 m1 走完整 probe 重试才 FAILED。
        # 证明 create_task 真起 + _one try/except 容错(不抛)+ 不阻塞 /health。
        deadline = time.monotonic() + 75
        while time.monotonic() < deadline and state.get_status("m1") != ModelStatus.FAILED:
            time.sleep(0.1)
        assert state.get_status("m1") == ModelStatus.FAILED
    # with 退出 → lifespan finally:stop_event.set() + unload_all + cancel+gather,干净关闭无异常


def test_create_app_warm_start_skips_import(tmp_path):
    """同库二次 create_app(无 legacy_yaml)→ 已 initialized → 跳过导入,保留 DB 状态。"""
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(_CFG_BODY, encoding="utf-8")
    create_app(db_path=tmp_path / "t.db", legacy_yaml=cfg_path)          # 首次:导入
    # 二次:不带 legacy_yaml,库已 initialized → seed/import 都跳过,仅 env 写库
    app2 = create_app(db_path=tmp_path / "t.db")
    assert "m1" in app2.state.cfg.models                                 # 保留首次导入的模型


def test_create_app_closes_db_on_bootstrap_error(tmp_path):
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "program: {host: 0.0.0.0, port: 8080, alive_time: 60, log_level: INFO}\n"
        "Local-Models:\n  A: {aliases: [x], mode: Chat, port: 1}\n"      # 无 scheme → validate 报错
        "  B: {aliases: [x], mode: Chat, port: 1}\n",                    # alias/port 冲突
        encoding="utf-8")
    db_path = tmp_path / "t.db"
    with pytest.raises(ValueError):
        create_app(db_path=db_path, legacy_yaml=cfg_path)
    # db.conn 应已关闭:可重新打开(Windows 上未关会锁文件)
    from llm_manager.data.persistence import open_db
    open_db(db_path).conn.execute("SELECT 1").fetchone()                 # 不抛


def test_crud_then_catalog_reflects_without_restart(tmp_path, monkeypatch):
    """P2 核心契约:POST /api/config/models 后,不重启即见 /v1/models + /api/models(读穿)。"""
    monkeypatch.setattr("llm_manager.devices.is_lhm_available", lambda: False)  # 隔离 LHM 慢枚举
    cfg_path = tmp_path / "config.yaml"
    cfg_path.write_text(
        "program: {host: 0.0.0.0, port: 8080, alive_time: 60, log_level: INFO}\n"
        "Local-Models:\n  A: {aliases: [a], mode: Chat, port: 9001,"
        "    S: {required_devices: [gpu], command: {exe: a.bat}, memory_mb: {gpu: 1}}}\n",
        encoding="utf-8")
    app = create_app(db_path=tmp_path / "t.db", legacy_yaml=cfg_path)
    with TestClient(app) as c:
        # 初始:A 在册
        assert "a" in {m["id"] for m in c.get("/v1/models").json()["data"]}
        # CRUD 加 B
        r = c.post("/api/config/models", json={
            "name": "B", "mode": "Chat", "port": 9002, "auto_start": False, "aliases": ["b"],
            "schemes": [{"config_source": "S", "required_devices": ["gpu"],
                         "command": {"exe": "b.bat"}, "memory_mb": {"gpu": 1}}]})
        assert r.status_code == 201
        # 不重启即见 B(读穿:/v1/models 与 /api/models 都走 config_store.snapshot)
        v1 = {m["id"] for m in c.get("/v1/models").json()["data"]}
        api = {m["alias"] for m in c.get("/api/models").json()["data"]}
        assert "b" in v1 and "b" in api
        # CRUD 删 A → 反映
        c.delete("/api/config/models/A")
        v1b = {m["id"] for m in c.get("/v1/models").json()["data"]}
        assert "a" not in v1b and "b" in v1b
