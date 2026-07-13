import time

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from llm_manager.data.config_store import ConfigStore, is_initialized, seed_defaults
from llm_manager.data.persistence import open_db
from llm_manager.gateway.api.config_api import register_config_routes


def _app(tmp_path):
    db = open_db(tmp_path / "t.db")
    if not is_initialized(db):              # warm-start(已初始化)跳过 seed,保留既有写入
        seed_defaults(db)
    store = ConfigStore(db, scripts_dir=tmp_path / "scripts")
    app = FastAPI()
    api = APIRouter(prefix="/api")
    register_config_routes(api)
    app.include_router(api)
    app.state.db = db
    app.state.config_store = store
    app.state.boot_program = {"host": "0.0.0.0", "port": "8080",
                              "db_path": "data/llm_manager.db", "log_dir": "logs"}
    app.state.started_at = time.time()
    return app


def test_system_info_returns_db_logdir_version(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/system/info")
    assert r.status_code == 200
    j = r.json()
    assert isinstance(j["version"], str)
    assert isinstance(j["started_at"], (int, float))
    assert j["log_dir"] == "logs"
    assert "db_path" in j


def test_get_config_returns_current_program_wol_claude_logs(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/config")
    assert r.status_code == 200
    j = r.json()
    assert j["program"]["host"] == "0.0.0.0"
    assert j["program"]["port"] == 8080
    assert j["program"]["log_level"] == "INFO"
    assert j["program"]["alive_time"] == 60
    assert j["wol"] is None
    assert j["claude"] == {}
    assert j["logs"] == {
        "time_enabled": False, "days": 30, "count_enabled": False, "count": 10,
    }
    assert j["restart_fields"] == []                # boot == snapshot → 无差异


def test_put_program_writes_and_reports_restart_on_port_change(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.put("/api/config/program", json={"port": 9000})
    assert r.status_code == 200
    j = r.json()
    assert set(j["restart_fields"]) == {"port"}        # port 改了 → 需重启
    assert j["needs_restart"] is True
    # 写入已生效:同库二次启动(warm-start,_app 跳过 seed)→ 读到 9000
    with TestClient(_app(tmp_path)) as c:
        assert c.get("/api/config").json()["program"]["port"] == 9000


def test_put_program_hot_field_no_restart(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.put("/api/config/program", json={"alive_time": 5})
    assert r.status_code == 200
    assert r.json()["needs_restart"] is False          # alive_time 热字段
    assert r.json()["restart_fields"] == []


def test_put_program_rejects_bad_port(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.put("/api/config/program", json={"port": 99999})   # >65535
    assert r.status_code == 422                          # Pydantic Field(le=65535)


def test_put_wol_writes_both_keys(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.put("/api/config/wol", json={"broadcast_address": "10.0.0.255", "mac_address": "aa:bb:cc:dd:ee:ff"})
    assert r.status_code == 200
    wol = c.get("/api/config").json()["wol"]
    assert wol == {"broadcast_address": "10.0.0.255", "mac_address": "aa:bb:cc:dd:ee:ff"}


def test_put_wol_rejects_partial_update(tmp_path):
    # WOL 是一对:只发一个字段 → 422(防 read_appconfig 的 wol 双键门槛造成静默孤儿)
    with TestClient(_app(tmp_path)) as c:
        r = c.put("/api/config/wol", json={"broadcast_address": "10.0.0.255"})
    assert r.status_code == 422


def test_put_claude_replaces_configs(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.put("/api/config/claude", json={"configs": {"GLM": {"ANTHROPIC_BASE_URL": "http://x"}}})
    assert r.status_code == 200
    assert c.get("/api/config").json()["claude"] == {"GLM": {"ANTHROPIC_BASE_URL": "http://x"}}


def test_put_logs_updates_retention_rules(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.put("/api/config/logs", json={"time_enabled": True, "days": 14, "count_enabled": False, "count": 10})
    assert r.status_code == 200
    assert c.get("/api/config").json()["logs"] == {
        "time_enabled": True, "days": 14, "count_enabled": False, "count": 10,
    }


def test_reload_reapplies_hot_log_level(tmp_path, monkeypatch):
    import llm_manager.app as appmod
    captured = {}
    def fake_setup(level="INFO", log_dir="logs"):
        captured["level"] = level
    monkeypatch.setattr(appmod, "setup_logging", fake_setup)
    with TestClient(_app(tmp_path)) as c:
        c.put("/api/config/program", json={"log_level": "DEBUG"})
        r = c.post("/api/config/reload")
    assert r.status_code == 200
    assert captured["level"] == "DEBUG"


def test_restart_status_no_change_no_serving(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/config/restart-status")
    assert r.status_code == 200
    j = r.json()
    assert j["needs_restart"] is False
    assert j["restart_fields"] == []
    assert j["serving"] == []


def test_restart_status_reports_changed_fields_and_serving(tmp_path):
    from llm_manager import state
    from llm_manager.state import ModelStatus
    state._reset()
    state.set_status("m1", ModelStatus.ROUTING, force=True)
    state.inc_pending("m1")
    try:
        with TestClient(_app(tmp_path)) as c:
            c.put("/api/config/program", json={"port": 9000})     # 改重启字段
            r = c.get("/api/config/restart-status")
        j = r.json()
        assert j["needs_restart"] is True
        assert set(j["restart_fields"]) == {"port"}
        assert j["serving"] == ["m1"]
    finally:
        state._reset()


def test_put_program_claude_settings_path_is_restart_classified(tmp_path):
    # claude_settings_path 改动需重启(tray 构造时捕获 _settings_path,不经 get_cfg)
    with TestClient(_app(tmp_path)) as c:
        r = c.put("/api/config/program", json={"claude_settings_path": "/new/settings.json"})
    assert r.status_code == 200
    j = r.json()
    assert "claude_settings_path" in j["restart_fields"]
    assert j["needs_restart"] is True
