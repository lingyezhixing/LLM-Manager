import hashlib
import json
from pathlib import Path

import pytest

from llm_manager.config import AppConfig, ModelConfig, ProgramConfig, Scheme, WakeOnLanConfig
from llm_manager.data.config_store import get_all_settings, get_setting, set_setting, write_appconfig
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
