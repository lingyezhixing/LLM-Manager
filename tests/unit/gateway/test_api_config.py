import time
import types

from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from llm_manager.data.config_store import ConfigStore, is_initialized, seed_defaults
from llm_manager.data.persistence import open_db
from llm_manager.gateway.api.config_api import register_config_routes


def _app(tmp_path):
    db = open_db(tmp_path / "t.db")
    if not is_initialized(db):  # warm-start(已初始化)跳过 seed,保留既有写入
        seed_defaults(db)
    store = ConfigStore(db)
    app = FastAPI()
    api = APIRouter(prefix="/api")
    register_config_routes(api)
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


def test_system_info_returns_version_uptime_db_size(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/system/info")
    assert r.status_code == 200
    j = r.json()
    # 锁住回归:version 必须从源码 pyproject 读到真实版本(3.0.0a2),不再滞后于
    # 已安装元数据。升版本号时同步改此处。
    assert j["version"] == "3.0.0a2"
    assert isinstance(j["started_at"], (int, float))
    assert "db_path" not in j and "log_dir" not in j  # 已移除:不再暴露路径
    assert isinstance(j["db_size_bytes"], int)  # 保留:实际库文件大小(fixture 挂了 resolved_db)


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
    assert j["logs"] == {"days": 30, "count": 10}
    assert j["restart_fields"] == []  # boot == snapshot → 无差异


def test_put_program_writes_and_reports_restart_on_port_change(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.put("/api/config/program", json={"port": 9000})
    assert r.status_code == 200
    j = r.json()
    assert set(j["restart_fields"]) == {"port"}  # port 改了 → 需重启
    assert j["needs_restart"] is True
    # 写入已生效:同库二次启动(warm-start,_app 跳过 seed)→ 读到 9000
    with TestClient(_app(tmp_path)) as c:
        assert c.get("/api/config").json()["program"]["port"] == 9000


def test_put_program_hot_field_no_restart(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.put("/api/config/program", json={"alive_time": 5})
    assert r.status_code == 200
    assert r.json()["needs_restart"] is False  # alive_time 热字段
    assert r.json()["restart_fields"] == []


def test_put_program_rejects_bad_port(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.put("/api/config/program", json={"port": 99999})  # >65535
    assert r.status_code == 422  # Pydantic Field(le=65535)


def test_put_logs_updates_retention_rules(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.put("/api/config/logs", json={"days": 14, "count": 20})
    assert r.status_code == 200
    assert c.get("/api/config").json()["logs"] == {
        "days": 14,
        "count": 20,
    }


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
            c.put("/api/config/program", json={"port": 9000})  # 改重启字段
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


def test_to_model_config_preserves_device_names():
    from llm_manager.gateway.api.config_api import (
        CommandInput,
        ModelDefInput,
        SchemeInput,
        _to_model_config,
    )

    body = ModelDefInput(
        name="M",
        mode="Chat",
        port=8000,
        aliases=["M"],
        schemes=[
            SchemeInput(
                config_source="RTX4060",
                required_devices=["RTX 4060"],
                command=CommandInput(exe="run"),
                memory_mb={"RTX 4060": 5120},
            )
        ],
    )
    mc = _to_model_config(body)
    assert mc.primary_name == "M"
    assert mc.aliases == ("M",)
    scheme = mc.schemes["RTX4060"]
    assert scheme.required_devices == frozenset({"RTX 4060"})  # 存储原样(所见即所存)
    assert scheme.memory_mb == {"RTX 4060": 5120}
    assert scheme.command.exe == "run" and scheme.command.args == ()


def test_to_model_config_rejects_duplicate_scheme_config_source():
    import pytest

    from llm_manager.gateway.api.config_api import (
        CommandInput,
        ModelDefInput,
        SchemeInput,
        _to_model_config,
    )

    body = ModelDefInput(
        name="M",
        mode="Chat",
        port=8000,
        aliases=["M"],
        schemes=[
            SchemeInput(config_source="S", required_devices=[], command=CommandInput(exe="a")),
            SchemeInput(config_source="S", required_devices=[], command=CommandInput(exe="b")),
        ],
    )
    with pytest.raises(ValueError):
        _to_model_config(body)


def _empty_cfg():
    from llm_manager.config import AppConfig, ProgramConfig

    return AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"), models={}, wol=None, claude_configs={}
    )


def _body(name="M", port=8000, aliases=None):
    from llm_manager.gateway.api.config_api import CommandInput, ModelDefInput, SchemeInput

    return ModelDefInput(
        name=name,
        mode="Chat",
        port=port,
        aliases=aliases or [name],
        schemes=[
            SchemeInput(
                config_source="S",
                required_devices=["gpu"],
                command=CommandInput(exe="run"),
                memory_mb={"gpu": 1},
            )
        ],
    )


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


def test_update_model_rename_swaps_key():
    from llm_manager.gateway.api.config_api import _create_model, _update_model

    cfg = _create_model(_empty_cfg(), _body("M"))
    cfg2 = _update_model(cfg, "M", _body("N"))
    assert "M" not in cfg2.models
    assert "N" in cfg2.models
    assert cfg2.models["N"].port == 8000


def test_update_model_rename_conflict_raises_model_exists():
    import pytest

    from llm_manager.data.config_store import ModelExists
    from llm_manager.gateway.api.config_api import _create_model, _update_model

    cfg = _create_model(_empty_cfg(), _body("M"))
    cfg = _create_model(cfg, _body("N"))
    with pytest.raises(ModelExists):
        _update_model(cfg, "M", _body("N"))  # M→N,但 N 已存在


def test_update_model_no_rename_replaces_in_place():
    from llm_manager.gateway.api.config_api import _create_model, _update_model

    cfg = _create_model(_empty_cfg(), _body("M", port=8000))
    cfg2 = _update_model(cfg, "M", _body("M", port=9000))  # 同名=非改名
    assert set(cfg2.models.keys()) == {"M"}
    assert cfg2.models["M"].port == 9000


def test_update_model_fn_replaces():
    from llm_manager.gateway.api.config_api import _create_model, _update_model

    cfg = _create_model(_empty_cfg(), _body("M", port=8000))
    cfg2 = _update_model(cfg, "M", _body("M", port=9000))
    assert cfg2.models["M"].port == 9000


def test_update_model_fn_raises_not_found():
    import pytest

    from llm_manager.data.config_store import ModelNotFound
    from llm_manager.gateway.api.config_api import _update_model

    with pytest.raises(ModelNotFound):
        _update_model(_empty_cfg(), "nope", _body("nope"))


def test_delete_model_fn_removes():
    from llm_manager.gateway.api.config_api import _create_model, _delete_model

    cfg = _create_model(_empty_cfg(), _body("M"))
    cfg2 = _delete_model(cfg, "M")
    assert cfg2.models == {}


def test_delete_model_fn_raises_not_found():
    import pytest

    from llm_manager.data.config_store import ModelNotFound
    from llm_manager.gateway.api.config_api import _delete_model

    with pytest.raises(ModelNotFound):
        _delete_model(_empty_cfg(), "nope")


def _def_body(name="M", port=8000, aliases=None, exe="run"):
    return {
        "name": name,
        "mode": "Chat",
        "port": port,
        "auto_start": False,
        "aliases": aliases or [name],
        "schemes": [
            {
                "config_source": "RTX4060",
                "required_devices": ["rtx 4060"],
                "command": {"exe": exe, "args": ["--port", str(port)]},
                "memory_mb": {"rtx 4060": 5120},
            }
        ],
    }


def test_post_model_def_creates_and_appears_in_list(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.post("/api/config/models", json=_def_body("M", 8000))
        assert r.status_code == 201
        assert r.json()["affected_routing"] == []  # 新模型未路由
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
    assert r.status_code == 422  # config.validate 拒非法 mode


def test_get_model_def_unknown_404(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        r = c.get("/api/config/models/nope")
    assert r.status_code == 404


def test_post_model_def_preserves_devices(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        body = _def_body("M")
        body["schemes"][0]["required_devices"] = ["RTX 4060"]
        body["schemes"][0]["memory_mb"] = {"RTX 4060": 5120}
        c.post("/api/config/models", json=body)
        one = c.get("/api/config/models/M").json()
    assert one["schemes"][0]["required_devices"] == ["RTX 4060"]  # 存储原样
    assert list(one["schemes"][0]["memory_mb"].keys()) == ["RTX 4060"]


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


def test_put_model_def_rename_without_migrate_deletes_logs(tmp_path):
    """改名(默认 migrate_data=false):cfg 换 key;请求记录(models)保留为孤立,旧日志删除。

    不迁移=旧身份废弃:日志随旧定义删(与 delete_model_def 一致,不留幽灵),
    请求记录留孤立(可由数据管理页清)。
    """
    from llm_manager.data.usage import resolve_model_id

    app = _app(tmp_path)
    with TestClient(app) as c:
        c.post("/api/config/models", json=_def_body("M"))
        resolve_model_id(app.state.db, "M")  # 造 models 行(用量/成本锚点)
        app.state.db.conn.execute(
            "INSERT INTO log_sessions (type, model_name, alias, start_time) "
            "VALUES ('model','M','M',0)"
        )
        app.state.db.conn.commit()
        r = c.put("/api/config/models/M", json=_def_body("N"))  # 改名,默认不迁移
        assert r.status_code == 200
        names = [m["name"] for m in c.get("/api/config/models").json()]
        assert "N" in names and "M" not in names
        # 请求记录保留为孤立(不迁移)
        rows = [
            row["original_name"]
            for row in app.state.db.conn.execute("SELECT original_name FROM models")
        ]
        assert rows == ["M"]
        # 旧日志已删(不迁移=旧身份废弃,日志随旧定义删,不留幽灵)
        ls = [
            row["model_name"]
            for row in app.state.db.conn.execute(
                "SELECT model_name FROM log_sessions WHERE type='model'"
            )
        ]
        assert ls == []


def test_put_model_def_rename_with_migrate_moves_data(tmp_path):
    """改名 + migrate_data=true:models.original_name 与 log_sessions.model_name 迁到新名,model_id 不变。"""
    from llm_manager.data.usage import resolve_model_id

    app = _app(tmp_path)
    with TestClient(app) as c:
        c.post("/api/config/models", json=_def_body("M"))
        mid = resolve_model_id(app.state.db, "M")
        app.state.db.conn.execute(
            "INSERT INTO log_sessions (type, model_name, alias, start_time) "
            "VALUES ('model','M','M',0)"
        )
        app.state.db.conn.commit()
        r = c.put("/api/config/models/M?migrate_data=true", json=_def_body("N"))
        assert r.status_code == 200
        rows = [
            row["original_name"]
            for row in app.state.db.conn.execute("SELECT original_name FROM models")
        ]
        assert rows == ["N"]
        # model_id 不变 → model_requests/model_runtime 仍关联
        new_id = app.state.db.conn.execute(
            "SELECT id FROM models WHERE original_name='N'"
        ).fetchone()["id"]
        assert new_id == mid
        ls = [
            row["model_name"]
            for row in app.state.db.conn.execute(
                "SELECT model_name FROM log_sessions WHERE type='model'"
            )
        ]
        assert ls == ["N"]
        # alias 快照同步为新配置 aliases[0](日志页按别名显示,不残留旧名快照)
        la = [
            row["alias"]
            for row in app.state.db.conn.execute(
                "SELECT alias FROM log_sessions WHERE type='model'"
            )
        ]
        assert la == ["N"]


def test_put_model_def_rename_migrate_syncs_alias_snapshot(tmp_path):
    """迁移时日志别名快照同步为新配置 aliases[0](改 primary + 别名都变时,旧日志不残留旧名)。"""
    app = _app(tmp_path)
    with TestClient(app) as c:
        c.post("/api/config/models", json=_def_body("M", aliases=["old-a"]))
        app.state.db.conn.execute(
            "INSERT INTO log_sessions (type, model_name, alias, start_time) "
            "VALUES ('model','M','old-a',0)"
        )
        app.state.db.conn.commit()
        body = _def_body("N", aliases=["new-a"])  # 改名 + 别名同时变
        r = c.put("/api/config/models/M?migrate_data=true", json=body)
        assert r.status_code == 200
        ls = [
            row["model_name"]
            for row in app.state.db.conn.execute(
                "SELECT model_name FROM log_sessions WHERE type='model'"
            )
        ]
        assert ls == ["N"]
        la = [
            row["alias"]
            for row in app.state.db.conn.execute(
                "SELECT alias FROM log_sessions WHERE type='model'"
            )
        ]
        assert la == ["new-a"]


def test_put_model_def_alias_change_syncs_log_alias_snapshot(tmp_path):
    """非改名但 aliases[0] 变更 → 日志 alias 快照同步为新别名(日志页按别名显示,不残留旧名)。"""
    app = _app(tmp_path)
    with TestClient(app) as c:
        c.post("/api/config/models", json=_def_body("M", aliases=["old-a"]))
        app.state.db.conn.execute(
            "INSERT INTO log_sessions (type, model_name, alias, start_time) "
            "VALUES ('model','M','old-a',0)"
        )
        app.state.db.conn.commit()
        r = c.put("/api/config/models/M", json=_def_body("M", aliases=["new-a"]))  # 同名,别名变
        assert r.status_code == 200
        la = [
            row["alias"]
            for row in app.state.db.conn.execute(
                "SELECT alias FROM log_sessions WHERE type='model'"
            )
        ]
        assert la == ["new-a"]


def test_put_model_def_same_alias_no_log_change(tmp_path):
    """非改名且别名不变 → 日志不动(不误同步)。"""
    app = _app(tmp_path)
    with TestClient(app) as c:
        c.post("/api/config/models", json=_def_body("M", aliases=["a1"]))
        app.state.db.conn.execute(
            "INSERT INTO log_sessions (type, model_name, alias, start_time) "
            "VALUES ('model','M','a1',0)"
        )
        app.state.db.conn.commit()
        r = c.put("/api/config/models/M", json=_def_body("M", aliases=["a1"]))  # 同名同别名
        assert r.status_code == 200
        la = [
            row["alias"]
            for row in app.state.db.conn.execute(
                "SELECT alias FROM log_sessions WHERE type='model'"
            )
        ]
        assert la == ["a1"]


def test_put_model_def_rename_conflict_409(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/config/models", json=_def_body("M", port=8000))
        c.post("/api/config/models", json=_def_body("N", port=9000))
        r = c.put("/api/config/models/M", json=_def_body("N", port=9000))  # M→N,N 已存在
        assert r.status_code == 409


def test_put_model_def_rename_active_409(tmp_path):
    """活跃态(ROUTING)改名 → 409(避免与 state/lifecycle 错位)。"""
    from llm_manager import state
    from llm_manager.state import ModelStatus

    state._reset()
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/config/models", json=_def_body("M"))
        state.set_status("M", ModelStatus.ROUTING, force=True)
        r = c.put("/api/config/models/M", json=_def_body("N"))
        assert r.status_code == 409
    state._reset()


def test_put_model_def_rename_migrate_new_name_occupied_422(tmp_path):
    """迁移时新名已被孤立数据占用 → 422(避免 UPDATE models 撞 UNIQUE)。"""
    from llm_manager.data.usage import resolve_model_id

    app = _app(tmp_path)
    with TestClient(app) as c:
        c.post("/api/config/models", json=_def_body("M"))
        resolve_model_id(app.state.db, "N")  # 孤立数据先占了 N
        app.state.db.conn.commit()
        r = c.put("/api/config/models/M?migrate_data=true", json=_def_body("N"))
        assert r.status_code == 422


def test_put_model_def_routing_returns_hint(tmp_path):
    from llm_manager import state
    from llm_manager.state import ModelStatus

    state._reset()
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/config/models", json=_def_body("M", 8000, aliases=["m-served"]))
        state.set_status("M", ModelStatus.ROUTING, force=True)  # 按 primary_name 置 ROUTING
        r = c.put("/api/config/models/M", json=_def_body("M", 9000, aliases=["m-served"]))
    assert r.status_code == 200
    j = r.json()
    assert j["affected_routing"] == ["m-served"]  # served name(aliases[0])
    assert j["hint"] == "restart_model"
    state._reset()


def test_delete_model_def_removes(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/config/models", json=_def_body("M"))
        r = c.delete("/api/config/models/M")
        assert r.status_code == 200
        assert c.get("/api/config/models").json() == []


def test_delete_model_def_removes_logs_keeps_usage(tmp_path):
    """删定义 → 连带删该模型日志会话(级联 log_lines),保留请求记录(成为孤立模型)。"""
    import asyncio

    from llm_manager.data import logs as _logs
    from llm_manager.data import persistence as _p
    from llm_manager.data.usage import record_usage

    try:
        with TestClient(_app(tmp_path)) as c:
            c.post("/api/config/models", json=_def_body("M"))
            db = c.app.state.db
            _logs.init(db)
            # M 的日志会话(落库行)+ 请求记录;N 的会话作对照组
            _logs.start_session("model", model_name="M", alias="M")
            _logs.capture("M", "hello", "out")
            asyncio.run(_logs.flush())
            _logs.start_session("model", model_name="N", alias="N")
            record_usage(
                db,
                "M",
                start=1.0,
                end=2.0,
                input_tokens=10,
                output_tokens=5,
                cache_n=0,
                prompt_n=10,
            )
            r = c.delete("/api/config/models/M")
            assert r.status_code == 200
            # 日志:M 的会话连同行级联删除;N 的会话保留
            assert (
                db.conn.execute(
                    "SELECT COUNT(*) FROM log_sessions WHERE model_name='M' OR alias='M'"
                ).fetchone()[0]
                == 0
            )
            assert db.conn.execute("SELECT COUNT(*) FROM log_lines").fetchone()[0] == 0
            assert (
                db.conn.execute(
                    "SELECT COUNT(*) FROM log_sessions WHERE model_name='N'"
                ).fetchone()[0]
                == 1
            )
            # 请求记录:M 保留(models 主档 + model_requests 行)→ 成为孤立模型
            assert (
                db.conn.execute("SELECT COUNT(*) FROM models WHERE original_name='M'").fetchone()[0]
                == 1
            )
            assert db.conn.execute("SELECT COUNT(*) FROM model_requests").fetchone()[0] == 1
            assert _p.orphaned_models(
                db, set(c.app.state.config_store.snapshot().models.keys())
            ) == ["M"]
    finally:
        _logs.reset()


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
    assert r.status_code == 409  # 避免 delete 留孤儿进程
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
        deadline = time.monotonic() + 2  # 内部 0.5s 延迟,2s 余量
        while time.monotonic() < deadline and not server.should_exit:
            time.sleep(0.05)
        assert server.should_exit is True


def test_restart_without_server_exits_81_after_flush(monkeypatch, tmp_path):
    """dev(--reload)模式无真实 uvicorn server:0.5s 冲刷延迟后 os._exit(81)(Dev-Backend.bat 81 循环重启)。"""
    calls: list[int] = []
    monkeypatch.setattr(
        "llm_manager.gateway.api.config_api.os._exit", lambda code: calls.append(code)
    )
    client = TestClient(_app(tmp_path))  # _app 不设 uvicorn_server(dev 模式等价)
    with client:
        r = client.post("/api/config/restart")
        assert r.status_code == 202
        deadline = time.monotonic() + 2  # 内部 0.5s 延迟,2s 余量
        while not calls and time.monotonic() < deadline:
            time.sleep(0.05)
    assert calls == [81]


def _def_body_with_pricing(name="M", port=8000):
    return {
        "name": name,
        "mode": "Chat",
        "port": port,
        "auto_start": False,
        "aliases": [name],
        "schemes": [
            {
                "config_source": "S",
                "required_devices": ["gpu"],
                "command": {"exe": "run"},
                "memory_mb": {"gpu": 1},
            }
        ],
        "pricing": {
            "pricing_type": "tier",
            "hourly_price": 2.5,
            "support_cache": True,
            "tiers": [
                {
                    "tier_index": 1,
                    "min_input": 0,
                    "max_input": 32768,
                    "min_output": 0,
                    "max_output": 32768,
                    "input_price": 3.0,
                    "output_price": 9.0,
                    "cache_write_price": 3.75,
                    "cache_read_price": 0.3,
                }
            ],
        },
    }


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
        assert "support_cache" not in t  # 已上移到模型级
        assert t["cache_write_price"] == 3.75 and t["cache_read_price"] == 0.3


def test_model_def_defaults_pricing_when_omitted(tmp_path):
    with TestClient(_app(tmp_path)) as c:
        c.post("/api/config/models", json=_def_body("M"))  # no pricing key
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


def test_put_program_ignores_removed_log_dir_db_path(tmp_path):
    # 已移除的底层键:静默忽略(不写入、不触发重启),配置响应永不出现
    with TestClient(_app(tmp_path)) as c:
        r = c.put("/api/config/program", json={"log_dir": "/evil", "db_path": "/evil.db"})
    assert r.status_code == 200
    assert r.json()["needs_restart"] is False
    p = c.get("/api/config").json()["program"]
    assert "log_dir" not in p and "db_path" not in p
