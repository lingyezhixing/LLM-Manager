import json
import time

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from llm_manager.data.config_store import ConfigStore, is_initialized, seed_defaults
from llm_manager.data.persistence import open_db
from llm_manager.gateway.api.config_api import register_config_routes
from llm_manager.gateway.api.tools_api import register_tools_routes


def _app(tmp_path):
    # 工具路由迁移后仍需 config 路由:测试经 /api/config/program 设 claude_settings_path,
    # 经 /api/config 读回 wol/claude 快照(只读投影仍在 config)。
    db = open_db(tmp_path / "t.db")
    if not is_initialized(db):  # warm-start(已初始化)跳过 seed
        seed_defaults(db)
    store = ConfigStore(db)
    app = FastAPI()
    api = APIRouter(prefix="/api")
    register_config_routes(api)
    register_tools_routes(api)
    app.include_router(api)
    app.state.db = db
    app.state.config_store = store
    app.state.boot_program = {
        "host": "0.0.0.0",
        "port": "8080",
        "claude_settings_path": None,
        "log_level": "INFO",
    }
    app.state.resolved_db = str(tmp_path / "t.db")
    app.state.started_at = time.time()
    return app


def test_put_wol_writes_both_keys(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.put(
            "/api/tools/wol",
            json={"broadcast_address": "10.0.0.255", "mac_address": "aa:bb:cc:dd:ee:ff"},
        )
    assert r.status_code == 200
    wol = c.get("/api/config").json()["wol"]
    assert wol == {"broadcast_address": "10.0.0.255", "mac_address": "aa:bb:cc:dd:ee:ff"}


def test_put_wol_rejects_partial_update(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.put("/api/tools/wol", json={"broadcast_address": "10.0.0.255"})
    assert r.status_code == 422


def test_delete_wol_clears_config(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.put(
            "/api/tools/wol",
            json={"broadcast_address": "10.0.0.255", "mac_address": "aa:bb:cc:dd:ee:ff"},
        )
        assert c.get("/api/config").json()["wol"] is not None
        r = c.delete("/api/tools/wol")
        assert r.status_code == 200
        assert r.json()["needs_restart"] is False
        assert c.get("/api/config").json()["wol"] is None
    with TestClient(_app(tmp_path)) as c:
        assert c.get("/api/config").json()["wol"] is None


def test_send_wol_now_posts_magic_packet(tmp_path, monkeypatch):
    from llm_manager.tools import wol as wol_module

    sent: list[tuple[str, str]] = []
    monkeypatch.setattr(
        wol_module, "send_wol", lambda mac, broadcast, port=9: sent.append((mac, broadcast))
    )
    with TestClient(_app(tmp_path)) as c:
        r = c.post(
            "/api/tools/wol/send",
            json={"broadcast_address": "10.0.0.255", "mac_address": "aa:bb:cc:dd:ee:ff"},
        )
        assert r.status_code == 200
        assert r.json() == {"ok": True}
    assert sent == [("aa:bb:cc:dd:ee:ff", "10.0.0.255")]


def test_send_wol_now_rejects_bad_mac(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.post(
            "/api/tools/wol/send",
            json={"broadcast_address": "10.0.0.255", "mac_address": "not-a-mac"},
        )
    assert r.status_code == 422


def test_put_claude_replaces_configs(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.put(
            "/api/tools/claude", json={"configs": {"GLM": {"ANTHROPIC_BASE_URL": "http://x"}}}
        )
    assert r.status_code == 200
    assert c.get("/api/config").json()["claude"] == {"GLM": {"ANTHROPIC_BASE_URL": "http://x"}}


def test_put_claude_save_and_apply_writes_settings(tmp_path):
    # 编辑当前生效预设:保存同时写 settings.json(PUT 带 apply)。非破坏:顶层/env 既有键保留。
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"apiKeyHelper": "off", "env": {"EXISTING": "1"}}), encoding="utf-8"
    )
    with TestClient(_app(tmp_path)) as c:
        c.put("/api/tools/claude", json={"configs": {"GLM": {"ANTHROPIC_BASE_URL": "http://glm"}}})
        c.put("/api/config/program", json={"claude_settings_path": str(settings)})
        r = c.put(
            "/api/tools/claude",
            json={
                "configs": {"GLM": {"ANTHROPIC_BASE_URL": "http://glm/v2"}},
                "apply": "GLM",
            },
        )
    assert r.status_code == 200
    assert r.json()["applied"] == "GLM"
    assert c.get("/api/config").json()["claude"] == {"GLM": {"ANTHROPIC_BASE_URL": "http://glm/v2"}}
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["apiKeyHelper"] == "off"  # 非破坏:顶层键保留
    assert data["env"]["EXISTING"] == "1"  # env 既有键保留
    assert data["env"]["ANTHROPIC_BASE_URL"] == "http://glm/v2"


def test_put_claude_apply_unknown_preset_404_not_saved(tmp_path):
    # apply 名不在新 configs → 404,DB 不落脏数据。
    with TestClient(_app(tmp_path)) as c:
        r = c.put(
            "/api/tools/claude",
            json={"configs": {"GLM": {"ANTHROPIC_BASE_URL": "http://x"}}, "apply": "NOPE"},
        )
    assert r.status_code == 404
    assert c.get("/api/config").json()["claude"] == {}


def test_put_claude_apply_without_path_400_not_saved(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.put(
            "/api/tools/claude",
            json={"configs": {"GLM": {"ANTHROPIC_BASE_URL": "http://x"}}, "apply": "GLM"},
        )
    assert r.status_code == 400
    assert c.get("/api/config").json()["claude"] == {}


def test_put_claude_apply_write_failure_500_saved(tmp_path):
    # settings 路径指向已存在目录 → 写失败 → 500;DB 已保存(单一事实源),settings.json 未动。
    with TestClient(_app(tmp_path)) as c:
        c.put("/api/config/program", json={"claude_settings_path": str(tmp_path)})
        r = c.put(
            "/api/tools/claude",
            json={"configs": {"GLM": {"ANTHROPIC_BASE_URL": "http://x"}}, "apply": "GLM"},
        )
    assert r.status_code == 500
    assert "写入 settings.json 失败" in r.json()["detail"]
    assert c.get("/api/config").json()["claude"] == {"GLM": {"ANTHROPIC_BASE_URL": "http://x"}}


def test_apply_claude_preset_writes_settings_preserving_other_keys(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"apiKeyHelper": "off", "env": {"EXISTING": "1"}}), encoding="utf-8"
    )
    with TestClient(_app(tmp_path)) as c:
        c.put("/api/tools/claude", json={"configs": {"GLM": {"ANTHROPIC_BASE_URL": "http://glm"}}})
        c.put("/api/config/program", json={"claude_settings_path": str(settings)})
        r = c.post("/api/tools/claude/apply", json={"name": "GLM"})
    assert r.status_code == 200
    assert r.json() == {"applied": "GLM"}
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["apiKeyHelper"] == "off"  # 非破坏:顶层键保留
    assert data["env"]["EXISTING"] == "1"  # env 既有键保留
    assert data["env"]["ANTHROPIC_BASE_URL"] == "http://glm"


def test_apply_claude_preset_unknown_preset_404(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.post("/api/tools/claude/apply", json={"name": "NOPE"})
    assert r.status_code == 404


def test_apply_claude_preset_without_path_400(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.put("/api/tools/claude", json={"configs": {"GLM": {"ANTHROPIC_BASE_URL": "http://glm"}}})
        r = c.post("/api/tools/claude/apply", json={"name": "GLM"})
    assert r.status_code == 400


def test_current_claude_preset_detects_by_base_url(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://glm/v1"}}), encoding="utf-8"
    )
    with TestClient(_app(tmp_path)) as c:
        c.put("/api/tools/claude", json={"configs": {"GLM": {"ANTHROPIC_BASE_URL": "http://glm"}}})
        c.put("/api/config/program", json={"claude_settings_path": str(settings)})
        r = c.get("/api/tools/claude/current")
    assert r.status_code == 200
    assert r.json() == {"current": "GLM"}


def test_current_claude_preset_unknown_without_settings(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.put("/api/tools/claude", json={"configs": {"GLM": {"ANTHROPIC_BASE_URL": "http://glm"}}})
        r = c.get("/api/tools/claude/current")
    assert r.status_code == 200
    assert r.json() == {"current": "(未知)"}


def test_apply_claude_preset_write_failure_500(tmp_path):
    # settings 路径指向已存在目录 → mkdir/write_text 抛 OSError → 500 带可读信息
    with TestClient(_app(tmp_path)) as c:
        c.put("/api/tools/claude", json={"configs": {"GLM": {"ANTHROPIC_BASE_URL": "http://glm"}}})
        c.put("/api/config/program", json={"claude_settings_path": str(tmp_path)})
        r = c.post("/api/tools/claude/apply", json={"name": "GLM"})
    assert r.status_code == 500
    assert "写入 settings.json 失败" in r.json()["detail"]
