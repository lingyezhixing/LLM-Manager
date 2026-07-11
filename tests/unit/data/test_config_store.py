import hashlib
import json
from pathlib import Path

import pytest

from llm_manager.config import AppConfig, ModelConfig, ProgramConfig, Scheme, WakeOnLanConfig
from llm_manager.data.config_store import (
    ConfigStore,
    apply_env_overrides,
    get_all_settings,
    get_setting,
    initialize,
    is_initialized,
    read_appconfig,
    seed_defaults,
    set_setting,
    write_appconfig,
)
from llm_manager.data.persistence import open_db


def test_set_get_setting_round_trip_and_upsert(tmp_path):
    db = open_db(tmp_path / "t.db")
    assert get_setting(db, "host") is None
    set_setting(db, "host", "0.0.0.0")
    assert get_setting(db, "host") == "0.0.0.0"
    set_setting(db, "host", "127.0.0.1")          # upsert 覆盖
    assert get_setting(db, "host") == "127.0.0.1"
    assert get_all_settings(db) == {"host": "127.0.0.1"}


def _sample_cfg(script_path: Path) -> AppConfig:
    scheme = Scheme(config_source="RTX4060", required_devices=frozenset({"rtx 4060"}),
                    script_path=script_path, memory_mb={"rtx 4060": 5120})
    return AppConfig(
        program=ProgramConfig(host="0.0.0.0", port=8080, alive_time=60, log_level="INFO"),
        models={"Qwen3-4B": ModelConfig(primary_name="Qwen3-4B", aliases=("Qwen3-4B", "q4"),
                                        mode="Chat", port=10001, auto_start=False,
                                        schemes={"RTX4060": scheme})},
        wol=WakeOnLanConfig("192.168.1.255", "aa:bb:cc:dd:ee:ff"),
        claude_configs={"GLM": {"ANTHROPIC_BASE_URL": "http://x"}},
    )


def test_write_appconfig_persists_program_wol_claude_and_model_world(tmp_path):
    db = open_db(tmp_path / "t.db")
    script = tmp_path / "q.bat"
    script.write_text("echo hi", encoding="utf-8")
    write_appconfig(db, _sample_cfg(script))

    assert get_setting(db, "host") == "0.0.0.0"
    assert get_setting(db, "port") == "8080"
    assert get_setting(db, "wol_broadcast") == "192.168.1.255"
    assert get_setting(db, "wol_mac") == "aa:bb:cc:dd:ee:ff"
    assert json.loads(get_setting(db, "claude_configs")) == {"GLM": {"ANTHROPIC_BASE_URL": "http://x"}}

    row = db.conn.execute("SELECT id, name, mode, port, auto_start FROM model_defs").fetchone()
    assert row["name"] == "Qwen3-4B" and row["mode"] == "Chat" and row["port"] == 10001
    mid = row["id"]
    aliases = [r["alias"] for r in db.conn.execute(
        "SELECT alias FROM model_aliases WHERE model_id=? ORDER BY ord", (mid,))]
    assert aliases == ["Qwen3-4B", "q4"]
    sc = db.conn.execute("SELECT config_source, required_devices, memory_mb FROM model_schemes").fetchone()
    assert sc["config_source"] == "RTX4060"
    assert json.loads(sc["required_devices"]) == ["rtx 4060"]
    assert json.loads(sc["memory_mb"]) == {"rtx 4060": 5120}
    srow = db.conn.execute("SELECT path, content, content_hash FROM model_scripts").fetchone()
    assert srow["path"] == str(script)
    assert srow["content"] == "echo hi"
    assert srow["content_hash"] == hashlib.sha256(b"echo hi").hexdigest()


def test_write_appconfig_replaces_model_world(tmp_path):
    db = open_db(tmp_path / "t.db")
    write_appconfig(db, _sample_cfg(tmp_path / "a.bat"))
    # 再写一个不同模型世界 → 旧的应被 CASCADE 清掉
    cfg2 = AppConfig(
        program=ProgramConfig(host="0.0.0.0", port=8080, alive_time=60, log_level="INFO"),
        models={"M2": ModelConfig(primary_name="M2", aliases=("M2",), mode="Chat", port=2)},
        wol=None, claude_configs={},
    )
    write_appconfig(db, cfg2)
    names = [r["name"] for r in db.conn.execute("SELECT name FROM model_defs")]
    assert names == ["M2"]


def test_write_appconfig_rolls_back_on_mid_write_failure(tmp_path):
    db = open_db(tmp_path / "t.db")
    # 先写入一个干净配置(已 commit)
    write_appconfig(db, AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
        models={"keep": ModelConfig("keep", ("keep",), "Chat", 1)}, wol=None, claude_configs={}))
    # 再写一个中途必失败的配置:两模型共用 alias "x" → UNIQUE(alias) 触发 IntegrityError
    bad = AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
        models={"A": ModelConfig("A", ("x",), "Chat", 1), "B": ModelConfig("B", ("x",), "Chat", 2)},
        wol=None, claude_configs={})
    with pytest.raises(Exception):
        write_appconfig(db, bad)
    # 模拟"后续无关 writer 的 commit"——若无 rollback,这里会冲刷孤儿 DELETE+partial(A),
    # 使 model_defs 变成 ["A"] 而非 ["keep"]。
    db.conn.commit()
    names = [r["name"] for r in db.conn.execute("SELECT name FROM model_defs")]
    assert names == ["keep"]   # rollback 生效:原模型世界完好,无 partial 残留


def test_read_appconfig_round_trips_back(tmp_path):
    db = open_db(tmp_path / "t.db")
    script = tmp_path / "q.bat"
    script.write_text("echo hi", encoding="utf-8")
    original = _sample_cfg(script)
    write_appconfig(db, original)

    out = read_appconfig(db, scripts_dir=tmp_path / "scripts")
    assert out.program.host == "0.0.0.0"
    assert out.program.port == 8080
    assert out.wol == WakeOnLanConfig("192.168.1.255", "aa:bb:cc:dd:ee:ff")
    assert out.claude_configs == {"GLM": {"ANTHROPIC_BASE_URL": "http://x"}}
    m = out.models["Qwen3-4B"]
    assert m.aliases == ("Qwen3-4B", "q4")
    assert m.mode == "Chat" and m.port == 10001
    scheme = m.schemes["RTX4060"]
    assert scheme.required_devices == frozenset({"rtx 4060"})
    assert scheme.memory_mb == {"rtx 4060": 5120}


def test_read_appconfig_materializes_script_content(tmp_path):
    db = open_db(tmp_path / "t.db")
    script = tmp_path / "q.bat"
    script.write_text("echo hi", encoding="utf-8")
    write_appconfig(db, _sample_cfg(script))

    out = read_appconfig(db, scripts_dir=tmp_path / "scripts")
    materialized = out.models["Qwen3-4B"].schemes["RTX4060"].script_path
    assert materialized != script                       # 指向物化文件,非原 path
    assert materialized.parent == (tmp_path / "scripts" / "Qwen3-4B")
    assert materialized.read_text(encoding="utf-8") == "echo hi"


def test_read_appconfig_falls_back_to_path_when_content_empty(tmp_path):
    """脚本文件导入时不可读(如测试里的 nonexistent.cmd)→ content 空 → 回退原 path。"""
    db = open_db(tmp_path / "t.db")
    scheme = Scheme(config_source="S", required_devices=frozenset({"gpu"}),
                    script_path=Path("nonexistent.cmd"), memory_mb={"gpu": 1})
    cfg = AppConfig(program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
                    models={"M": ModelConfig("M", ("M",), "Chat", 1, False, {"S": scheme})},
                    wol=None, claude_configs={})
    write_appconfig(db, cfg)                            # read_scripts 读不到 → content ""
    out = read_appconfig(db, scripts_dir=tmp_path / "scripts")
    assert out.models["M"].schemes["S"].script_path == Path("nonexistent.cmd")


def test_read_appconfig_empty_db_returns_defaults(tmp_path):
    db = open_db(tmp_path / "t.db")
    out = read_appconfig(db, scripts_dir=tmp_path / "scripts")
    assert out.models == {}
    assert out.wol is None
    assert out.claude_configs == {}
    assert out.program.host == "0.0.0.0" and out.program.port == 8080


def test_materialize_overwrites_corrupt_target(tmp_path):
    db = open_db(tmp_path / "t.db")
    script = tmp_path / "q.bat"
    script.write_text("echo hi", encoding="utf-8")
    write_appconfig(db, _sample_cfg(script))
    scripts_dir = tmp_path / "scripts"
    # 预置一个损坏的(截断多字节)物化目标——read_text 会 UnicodeDecodeError
    target = scripts_dir / "Qwen3-4B" / "RTX4060.bat"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"h\xc3")
    # 修复前:read_appconfig 在此崩溃(UnicodeDecodeError 传播);修复后:识别为损坏→重写
    out = read_appconfig(db, scripts_dir=scripts_dir)
    assert target.read_text(encoding="utf-8") == "echo hi"
    assert out.models["Qwen3-4B"].schemes["RTX4060"].script_path == target


def test_config_store_snapshot_and_reload(tmp_path):
    db = open_db(tmp_path / "t.db")
    write_appconfig(db, AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
        models={"M": ModelConfig("M", ("M",), "Chat", 1)}, wol=None, claude_configs={}))
    store = ConfigStore(db, scripts_dir=tmp_path / "scripts")
    assert "M" in store.snapshot().models

    # 直接改 DB,reload 后看到新值(P1 写回将走同一路径)
    set_setting(db, "port", "9999")
    snap = store.reload()
    assert snap.program.port == 9999


def test_is_initialized_false_on_fresh_db(tmp_path):
    assert is_initialized(open_db(tmp_path / "t.db")) is False


def test_seed_defaults_marks_initialized(tmp_path):
    db = open_db(tmp_path / "t.db")
    seed_defaults(db)
    assert is_initialized(db) is True
    assert get_setting(db, "host") == "0.0.0.0"
    assert get_setting(db, "log_retention_time_enabled") == "0"


def test_initialize_imports_legacy_yaml_when_db_empty(tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "program: {host: 0.0.0.0, port: 8080, alive_time: 60, log_level: INFO}\n"
        "Local-Models:\n"
        "  Qwen3-4B:\n"
        "    aliases: [\"Qwen3-4B\"]\n"
        "    mode: Chat\n"
        "    port: 10001\n"
        "    RTX4060:\n"
        "      required_devices: [\"rtx 4060\"]\n"
        "      script_path: \"q.bat\"\n"
        "      memory_mb: {\"rtx 4060\": 5120}\n",
        encoding="utf-8")
    db = open_db(tmp_path / "t.db")
    initialize(db, legacy_yaml=yaml_path)
    out = read_appconfig(db, scripts_dir=tmp_path / "scripts")
    assert "Qwen3-4B" in out.models
    assert out.models["Qwen3-4B"].schemes["RTX4060"].required_devices == frozenset({"rtx 4060"})


def test_initialize_skips_import_when_already_initialized(tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "program: {host: 0.0.0.0, port: 8080, alive_time: 60, log_level: INFO}\n"
        "Local-Models:\n  X: {aliases: [x], mode: Chat, port: 1, S: {required_devices: [gpu], script_path: a.bat, memory_mb: {gpu: 1}}}\n",
        encoding="utf-8")
    db = open_db(tmp_path / "t.db")
    initialize(db, legacy_yaml=yaml_path)           # 第一次:导入 X
    # 把模型世界清空(模拟后续手动 DB 状态),再 initialize → 不应重新导入
    with db.write_lock:
        db.conn.execute("DELETE FROM model_defs")
        db.conn.commit()
    initialize(db, legacy_yaml=yaml_path)           # 已 initialized(system_settings 非空)→ 跳过
    assert read_appconfig(db, scripts_dir=tmp_path / "scripts").models == {}


def test_apply_env_overrides_writes_set_env(monkeypatch, tmp_path):
    db = open_db(tmp_path / "t.db")
    seed_defaults(db)
    monkeypatch.setenv("LLM_MANAGER_PORT", "7000")
    monkeypatch.setenv("LLM_MANAGER_HOST", "127.0.0.1")
    apply_env_overrides(db)
    assert get_setting(db, "port") == "7000"
    assert get_setting(db, "host") == "127.0.0.1"


def test_apply_env_overrides_ignores_unset_env(monkeypatch, tmp_path):
    db = open_db(tmp_path / "t.db")
    seed_defaults(db)
    monkeypatch.delenv("LLM_MANAGER_PORT", raising=False)
    apply_env_overrides(db)
    assert get_setting(db, "port") == "8080"        # 默认值不动


def test_initialize_applies_env_after_import(monkeypatch, tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text("program: {host: 0.0.0.0, port: 8080, alive_time: 60, log_level: INFO}\n",
                         encoding="utf-8")
    monkeypatch.setenv("LLM_MANAGER_PORT", "7000")
    db = open_db(tmp_path / "t.db")
    initialize(db, legacy_yaml=yaml_path)
    assert get_setting(db, "port") == "7000"        # env 覆盖导入值


def test_initialize_rejects_invalid_yaml_and_leaves_db_clean(tmp_path):
    yaml_path = tmp_path / "config.yaml"
    # 模型 M 缺 aliases → config.validate 报 "has no aliases" → initialize 抛 ValueError,不写库
    yaml_path.write_text(
        "program: {host: 0.0.0.0, port: 8080, alive_time: 60, log_level: INFO}\n"
        "Local-Models:\n"
        "  M:\n"
        "    mode: Chat\n"
        "    port: 1\n"
        "    S:\n"
        "      required_devices: [gpu]\n"
        "      script_path: a.bat\n"
        "      memory_mb: {gpu: 1}\n",
        encoding="utf-8")
    db = open_db(tmp_path / "t.db")
    with pytest.raises(ValueError):
        initialize(db, legacy_yaml=yaml_path)
    # validate 在 write_appconfig 之前 → DB 干净,gate 未翻
    assert is_initialized(db) is False
    assert read_appconfig(db, scripts_dir=tmp_path / "scripts").models == {}


def test_initialize_failed_import_keeps_gate_open_for_recovery(tmp_path, monkeypatch):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "program: {host: 0.0.0.0, port: 8080, alive_time: 60, log_level: INFO}\n"
        "Local-Models:\n"
        "  M: {aliases: [m], mode: Chat, port: 1, S: {required_devices: [gpu], script_path: a.bat, memory_mb: {gpu: 1}}}\n",
        encoding="utf-8")
    db = open_db(tmp_path / "t.db")

    # 模拟导入期 DB 失败(如磁盘满):write_appconfig 抛 → initialize 不吞错,gate 保持开
    def boom(db, cfg, **kw):
        raise RuntimeError("disk full")

    import llm_manager.data.config_store as cs
    monkeypatch.setattr(cs, "write_appconfig", boom)
    with pytest.raises(RuntimeError):
        initialize(db, legacy_yaml=yaml_path)
    assert is_initialized(db) is False

    # 恢复真实 write_appconfig,第二次 initialize 正常导入
    monkeypatch.undo()
    initialize(db, legacy_yaml=yaml_path)
    assert is_initialized(db) is True
    assert "M" in read_appconfig(db, scripts_dir=tmp_path / "scripts").models
