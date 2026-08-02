import json
import time
import types

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from llm_manager.data.config_store import ConfigStore, is_initialized, seed_defaults
from llm_manager.data.persistence import open_db
from llm_manager.gateway.api.config_api import register_config_routes


def _app(tmp_path):
    db = open_db(tmp_path / "t.db")
    if not is_initialized(db):              # warm-start(已初始化)跳过 seed,保留既有写入
        seed_defaults(db)
    store = ConfigStore(db)
    app = FastAPI()
    api = APIRouter(prefix="/api")
    register_config_routes(api)
    app.include_router(api)
    app.state.db = db
    app.state.config_store = store
    app.state.boot_program = {"host": "0.0.0.0", "port": "8080",
                              "db_path": "data/llm_manager.db", "log_dir": "logs", "log_level": "INFO"}
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


def test_reload_no_longer_hot_applies_log_level(tmp_path, monkeypatch):
    """L1: log_level 归重启类,reload 不再调 setup_logging(消除 handler 重复/log_dir 矛盾 bug 面)。"""
    import llm_manager.app as appmod
    captured: dict = {}
    def fake_setup(*a, **k):
        captured["called"] = True
    monkeypatch.setattr(appmod, "setup_logging", fake_setup)
    with TestClient(_app(tmp_path)) as c:
        c.put("/api/config/program", json={"log_level": "DEBUG"})
        r = c.post("/api/config/reload")
    assert r.status_code == 200
    assert captured.get("called") is not True          # reload 不再触发热重配
    assert "log_level" in c.get("/api/config/restart-status").json()["restart_fields"]


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


def test_to_model_config_normalizes_device_names():
    from llm_manager.gateway.api.config_api import CommandInput, ModelDefInput, SchemeInput, _to_model_config
    body = ModelDefInput(name="M", mode="Chat", port=8000, aliases=["M"],
                         schemes=[SchemeInput(config_source="RTX4060",
                                              required_devices=["RTX 4060"],
                                              command=CommandInput(exe="run"),
                                              memory_mb={"RTX 4060": 5120})])
    mc = _to_model_config(body)
    assert mc.primary_name == "M"
    assert mc.aliases == ("M",)
    scheme = mc.schemes["RTX4060"]
    assert scheme.required_devices == frozenset({"rtx 4060"})     # 归一化(小写+strip)
    assert scheme.memory_mb == {"rtx 4060": 5120}
    assert scheme.command.exe == "run" and scheme.command.args == ()


def test_to_model_config_rejects_duplicate_scheme_config_source():
    from llm_manager.gateway.api.config_api import CommandInput, ModelDefInput, SchemeInput, _to_model_config
    import pytest
    body = ModelDefInput(name="M", mode="Chat", port=8000, aliases=["M"],
                         schemes=[SchemeInput(config_source="S", required_devices=[], command=CommandInput(exe="a")),
                                  SchemeInput(config_source="S", required_devices=[], command=CommandInput(exe="b"))])
    with pytest.raises(ValueError):
        _to_model_config(body)


def _empty_cfg():
    from llm_manager.config import AppConfig, ProgramConfig
    return AppConfig(program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
                     models={}, wol=None, claude_configs={})


def _body(name="M", port=8000, aliases=None):
    from llm_manager.gateway.api.config_api import CommandInput, ModelDefInput, SchemeInput
    return ModelDefInput(name=name, mode="Chat", port=port, aliases=aliases or [name],
                         schemes=[SchemeInput(config_source="S", required_devices=["gpu"],
                                              command=CommandInput(exe="run"), memory_mb={"gpu": 1})])


def test_create_model_fn_adds_model():
    from llm_manager.gateway.api.config_api import _create_model
    cfg = _create_model(_empty_cfg(), _body("M"))
    assert "M" in cfg.models and cfg.models["M"].port == 8000


def test_create_model_fn_raises_model_exists():
    from llm_manager.data.config_store import ModelExists
    from llm_manager.gateway.api.config_api import _create_model
    cfg = _create_model(_empty_cfg(), _body("M"))
    import pytest
    with pytest.raises(ModelExists):
        _create_model(cfg, _body("M"))


def test_update_model_fn_replaces():
    from llm_manager.gateway.api.config_api import _create_model, _update_model
    cfg = _create_model(_empty_cfg(), _body("M", port=8000))
    cfg2 = _update_model(cfg, "M", _body("M", port=9000))
    assert cfg2.models["M"].port == 9000


def test_update_model_fn_rejects_rename():
    from llm_manager.gateway.api.config_api import _create_model, _update_model
    cfg = _create_model(_empty_cfg(), _body("M"))
    import pytest
    with pytest.raises(ValueError):
        _update_model(cfg, "M", _body("Other"))        # body.name != path name


def test_update_model_fn_raises_not_found():
    from llm_manager.data.config_store import ModelNotFound
    from llm_manager.gateway.api.config_api import _update_model
    import pytest
    with pytest.raises(ModelNotFound):
        _update_model(_empty_cfg(), "nope", _body("nope"))


def test_delete_model_fn_removes():
    from llm_manager.gateway.api.config_api import _create_model, _delete_model
    cfg = _create_model(_empty_cfg(), _body("M"))
    cfg2 = _delete_model(cfg, "M")
    assert cfg2.models == {}


def test_delete_model_fn_raises_not_found():
    from llm_manager.data.config_store import ModelNotFound
    from llm_manager.gateway.api.config_api import _delete_model
    import pytest
    with pytest.raises(ModelNotFound):
        _delete_model(_empty_cfg(), "nope")


def _def_body(name="M", port=8000, aliases=None, exe="run"):
    return {"name": name, "mode": "Chat", "port": port, "auto_start": False,
            "aliases": aliases or [name],
            "schemes": [{"config_source": "RTX4060", "required_devices": ["rtx 4060"],
                         "command": {"exe": exe, "args": ["--port", str(port)]},
                         "memory_mb": {"rtx 4060": 5120}}]}


def test_post_model_def_creates_and_appears_in_list(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.post("/api/config/models", json=_def_body("M", 8000))
        assert r.status_code == 201
        assert r.json()["affected_routing"] == []          # 新模型未路由
        listed = c.get("/api/config/models").json()
        assert [m["name"] for m in listed] == ["M"]
        one = c.get("/api/config/models/M").json()
        assert one["name"] == "M" and one["port"] == 8000
        assert one["schemes"][0]["command"]["exe"] == "run"


def test_post_model_def_duplicate_name_409(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/config/models", json=_def_body("M"))
        r = c.post("/api/config/models", json=_def_body("M"))
    assert r.status_code == 409


def test_post_model_def_alias_clash_422(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/config/models", json=_def_body("M", 8000, aliases=["M"]))
        r = c.post("/api/config/models", json=_def_body("N", 8001, aliases=["M"]))  # alias "M" 冲突
    assert r.status_code == 422


def test_post_model_def_bad_mode_422(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        body = _def_body("M")
        body["mode"] = "Bogus"
        r = c.post("/api/config/models", json=body)
    assert r.status_code == 422                            # config.validate 拒非法 mode


def test_get_model_def_unknown_404(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/config/models/nope")
    assert r.status_code == 404


def test_post_model_def_normalizes_devices(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        body = _def_body("M")
        body["schemes"][0]["required_devices"] = ["RTX 4060"]
        body["schemes"][0]["memory_mb"] = {"RTX 4060": 5120}
        c.post("/api/config/models", json=body)
        one = c.get("/api/config/models/M").json()
    assert one["schemes"][0]["required_devices"] == ["rtx 4060"]   # 归一化
    assert list(one["schemes"][0]["memory_mb"].keys()) == ["rtx 4060"]


def test_put_model_def_replaces(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/config/models", json=_def_body("M", 8000))
        r = c.put("/api/config/models/M", json=_def_body("M", 9000))
        assert r.status_code == 200
        assert c.get("/api/config/models/M").json()["port"] == 9000


def test_put_model_def_unknown_404(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.put("/api/config/models/nope", json=_def_body("nope"))
    assert r.status_code == 404


def test_put_model_def_rename_422(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/config/models", json=_def_body("M"))
        r = c.put("/api/config/models/M", json=_def_body("Other"))   # body.name≠path
    assert r.status_code == 422


def test_put_model_def_routing_returns_hint(tmp_path):
    from llm_manager import state
    from llm_manager.state import ModelStatus
    state._reset()
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/config/models", json=_def_body("M", 8000, aliases=["m-served"]))
        state.set_status("M", ModelStatus.ROUTING, force=True)       # 按 primary_name 置 ROUTING
        r = c.put("/api/config/models/M", json=_def_body("M", 9000, aliases=["m-served"]))
    assert r.status_code == 200
    j = r.json()
    assert j["affected_routing"] == ["m-served"]                     # served name(aliases[0])
    assert j["hint"] == "restart_model"
    state._reset()


def test_delete_model_def_removes(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/config/models", json=_def_body("M"))
        r = c.delete("/api/config/models/M")
        assert r.status_code == 200
        assert c.get("/api/config/models").json() == []


def test_delete_model_def_unknown_404(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.delete("/api/config/models/nope")
    assert r.status_code == 404


def test_delete_model_def_routing_409(tmp_path):
    from llm_manager import state
    from llm_manager.state import ModelStatus
    state._reset()
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/config/models", json=_def_body("M", 8000))
        state.set_status("M", ModelStatus.ROUTING, force=True)
        r = c.delete("/api/config/models/M")
    assert r.status_code == 409                                      # 避免 delete 留孤儿进程
    state._reset()


def test_put_program_log_level_is_restart_class(tmp_path):
    """L1: log_level 降级为重启字段——改之须出现在 restart_fields。
    (现状:PUT 不热生效 + log_level 不在 _RESTART_FIELDS → 静默丢失到下次重启。)"""
    with TestClient(_app(tmp_path)) as c:
        r = c.put("/api/config/program", json={"log_level": "DEBUG"})
    assert r.status_code == 200
    j = r.json()
    assert "log_level" in j["restart_fields"]
    assert j["needs_restart"] is True


def test_restart_app_sets_flag_and_returns_202(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.post("/api/config/restart")
        assert r.status_code == 202
        assert c.app.state.restart_requested is True


def test_restart_app_flips_should_exit_when_server_present(tmp_path):
    # 模拟生产:app.state.uvicorn_server 存在 → 端点延迟(0.5s)翻 should_exit
    server = types.SimpleNamespace(should_exit=False)
    with TestClient(_app(tmp_path)) as c:
        c.app.state.uvicorn_server = server
        c.post("/api/config/restart")
        deadline = time.monotonic() + 2          # 内部 0.5s 延迟,2s 余量
        while time.monotonic() < deadline and not server.should_exit:
            time.sleep(0.05)
        assert server.should_exit is True


def _def_body_with_pricing(name="M", port=8000):
    return {"name": name, "mode": "Chat", "port": port, "auto_start": False,
            "aliases": [name],
            "schemes": [{"config_source": "S", "required_devices": ["gpu"],
                         "command": {"exe": "run"}, "memory_mb": {"gpu": 1}}],
            "pricing": {"pricing_type": "tier", "hourly_price": 2.5, "support_cache": True,
                        "tiers": [{"tier_index": 1, "min_input": 0, "max_input": 32768,
                                   "min_output": 0, "max_output": 32768,
                                   "input_price": 3.0, "output_price": 9.0,
                                   "cache_write_price": 3.75,
                                   "cache_read_price": 0.3}]}}


def test_model_def_pricing_round_trips_through_api(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.post("/api/config/models", json=_def_body_with_pricing("M"))
        assert r.status_code == 201
        one = c.get("/api/config/models/M").json()
        assert one["pricing"]["pricing_type"] == "tier"
        assert one["pricing"]["hourly_price"] == 2.5
        assert one["pricing"]["support_cache"] is True
        t = one["pricing"]["tiers"][0]
        assert t["tier_index"] == 1
        assert t["min_input"] == 0 and t["max_input"] == 32768
        assert t["min_output"] == 0 and t["max_output"] == 32768
        assert t["input_price"] == 3.0 and t["output_price"] == 9.0
        assert "support_cache" not in t                     # 已上移到模型级
        assert t["cache_write_price"] == 3.75 and t["cache_read_price"] == 0.3


def test_model_def_defaults_pricing_when_omitted(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/config/models", json=_def_body("M"))   # no pricing key
        one = c.get("/api/config/models/M").json()
        assert one["pricing"]["pricing_type"] == "tier"
        assert one["pricing"]["tiers"] == []
        assert one["pricing"]["hourly_price"] == 0.0
        assert one["pricing"]["support_cache"] is False


def test_model_def_rejects_bogus_pricing_type(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        body = _def_body("M")
        body["pricing"] = {"pricing_type": "Tier", "hourly_price": 0, "tiers": []}
        r = c.post("/api/config/models", json=body)
    assert r.status_code == 422


def test_apply_claude_preset_writes_settings_preserving_other_keys(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"apiKeyHelper": "off", "env": {"EXISTING": "1"}}), encoding="utf-8")
    with TestClient(_app(tmp_path)) as c:
        c.put("/api/config/claude", json={"configs": {"GLM": {"ANTHROPIC_BASE_URL": "http://glm"}}})
        c.put("/api/config/program", json={"claude_settings_path": str(settings)})
        r = c.post("/api/config/claude/apply", json={"name": "GLM"})
    assert r.status_code == 200
    assert r.json() == {"applied": "GLM"}
    data = json.loads(settings.read_text(encoding="utf-8"))
    assert data["apiKeyHelper"] == "off"                # 非破坏:顶层键保留
    assert data["env"]["EXISTING"] == "1"               # env 既有键保留
    assert data["env"]["ANTHROPIC_BASE_URL"] == "http://glm"


def test_apply_claude_preset_unknown_preset_404(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.post("/api/config/claude/apply", json={"name": "NOPE"})
    assert r.status_code == 404


def test_apply_claude_preset_without_path_400(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.put("/api/config/claude", json={"configs": {"GLM": {"ANTHROPIC_BASE_URL": "http://glm"}}})
        r = c.post("/api/config/claude/apply", json={"name": "GLM"})
    assert r.status_code == 400


def test_current_claude_preset_detects_by_base_url(tmp_path):
    settings = tmp_path / "settings.json"
    settings.write_text(json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://glm/v1"}}), encoding="utf-8")
    with TestClient(_app(tmp_path)) as c:
        c.put("/api/config/claude", json={"configs": {"GLM": {"ANTHROPIC_BASE_URL": "http://glm"}}})
        c.put("/api/config/program", json={"claude_settings_path": str(settings)})
        r = c.get("/api/config/claude/current")
    assert r.status_code == 200
    assert r.json() == {"current": "GLM"}


def test_current_claude_preset_unknown_without_settings(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.put("/api/config/claude", json={"configs": {"GLM": {"ANTHROPIC_BASE_URL": "http://glm"}}})
        r = c.get("/api/config/claude/current")
    assert r.status_code == 200
    assert r.json() == {"current": "(未知)"}


def test_apply_claude_preset_write_failure_500(tmp_path):
    # settings 路径指向已存在目录 → mkdir/write_text 抛 OSError → 500 带可读信息
    with TestClient(_app(tmp_path)) as c:
        c.put("/api/config/claude", json={"configs": {"GLM": {"ANTHROPIC_BASE_URL": "http://glm"}}})
        c.put("/api/config/program", json={"claude_settings_path": str(tmp_path)})
        r = c.post("/api/config/claude/apply", json={"name": "GLM"})
    assert r.status_code == 500
    assert "写入 settings.json 失败" in r.json()["detail"]
