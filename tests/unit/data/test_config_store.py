import json

import pytest
from dataclasses import replace

from llm_manager.config import (
    AppConfig,
    Command,
    ModelConfig,
    ProgramConfig,
    Scheme,
    WakeOnLanConfig,
)
from llm_manager.data.config_store import (
    ConfigStore,
    ConfigValidationFailed,
    _read_appconfig_locked,
    _write_appconfig_locked,
    apply_env_overrides,
    get_all_settings,
    get_setting,
    initialize,
    is_initialized,
    mutate_appconfig,
    read_appconfig,
    seed_defaults,
    set_setting,
    set_settings,
    write_appconfig,
)
from llm_manager.data.persistence import open_db


def test_set_get_setting_round_trip_and_upsert(tmp_path):
    db = open_db(tmp_path / "t.db")
    assert get_setting(db, "host") is None
    set_setting(db, "host", "0.0.0.0")
    assert get_setting(db, "host") == "0.0.0.0"
    set_setting(db, "host", "127.0.0.1")  # upsert 覆盖
    assert get_setting(db, "host") == "127.0.0.1"
    assert get_all_settings(db) == {"host": "127.0.0.1"}


def _sample_cfg() -> AppConfig:
    scheme = Scheme(
        config_source="RTX4060",
        required_devices=frozenset({"rtx 4060"}),
        command=Command(exe="q", args=("echo", "hi")),
        memory_mb={"rtx 4060": 5120},
    )
    return AppConfig(
        program=ProgramConfig(host="0.0.0.0", port=8080, alive_time=60, log_level="INFO"),
        models={
            "Qwen3-4B": ModelConfig(
                primary_name="Qwen3-4B",
                aliases=("Qwen3-4B", "q4"),
                mode="Chat",
                port=10001,
                auto_start=False,
                schemes={"RTX4060": scheme},
            )
        },
        wol=WakeOnLanConfig("192.168.1.255", "aa:bb:cc:dd:ee:ff"),
        claude_configs={"GLM": {"ANTHROPIC_BASE_URL": "http://x"}},
    )


def test_write_appconfig_persists_program_wol_claude_and_model_world(tmp_path):
    db = open_db(tmp_path / "t.db")
    write_appconfig(db, _sample_cfg())

    assert get_setting(db, "host") == "0.0.0.0"
    assert get_setting(db, "port") == "8080"
    assert get_setting(db, "wol_broadcast") == "192.168.1.255"
    assert get_setting(db, "wol_mac") == "aa:bb:cc:dd:ee:ff"
    assert json.loads(get_setting(db, "claude_configs")) == {
        "GLM": {"ANTHROPIC_BASE_URL": "http://x"}
    }

    row = db.conn.execute("SELECT id, name, mode, port, auto_start FROM model_defs").fetchone()
    assert row["name"] == "Qwen3-4B" and row["mode"] == "Chat" and row["port"] == 10001
    mid = row["id"]
    aliases = [
        r["alias"]
        for r in db.conn.execute(
            "SELECT alias FROM model_aliases WHERE model_id=? ORDER BY ord", (mid,)
        )
    ]
    assert aliases == ["Qwen3-4B", "q4"]
    sc = db.conn.execute(
        "SELECT config_source, required_devices, memory_mb, command FROM model_schemes"
    ).fetchone()
    assert sc["config_source"] == "RTX4060"
    assert json.loads(sc["required_devices"]) == ["rtx 4060"]
    assert json.loads(sc["memory_mb"]) == {"rtx 4060": 5120}
    cmd = json.loads(sc["command"])
    assert cmd["exe"] == "q"
    assert cmd["args"] == ["echo", "hi"]


def test_write_appconfig_replaces_model_world(tmp_path):
    db = open_db(tmp_path / "t.db")
    write_appconfig(db, _sample_cfg())
    # 再写一个不同模型世界 → 旧的应被 CASCADE 清掉
    cfg2 = AppConfig(
        program=ProgramConfig(host="0.0.0.0", port=8080, alive_time=60, log_level="INFO"),
        models={
            "M2": ModelConfig(
                primary_name="M2",
                aliases=("M2",),
                mode="Chat",
                port=2,
                schemes={"S": Scheme("S", frozenset({"gpu"}), Command(exe="m2"), {"gpu": 1})},
            )
        },
        wol=None,
        claude_configs={},
    )
    write_appconfig(db, cfg2)
    names = [r["name"] for r in db.conn.execute("SELECT name FROM model_defs")]
    assert names == ["M2"]


def test_write_appconfig_rolls_back_on_mid_write_failure(tmp_path):
    db = open_db(tmp_path / "t.db")
    # 先写入一个干净配置(已 commit)
    write_appconfig(
        db,
        AppConfig(
            program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
            models={"keep": ModelConfig("keep", ("keep",), "Chat", 1)},
            wol=None,
            claude_configs={},
        ),
    )
    # 再写一个中途必失败的配置:两模型共用 alias "x" → UNIQUE(alias) 触发 IntegrityError
    bad = AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
        models={"A": ModelConfig("A", ("x",), "Chat", 1), "B": ModelConfig("B", ("x",), "Chat", 2)},
        wol=None,
        claude_configs={},
    )
    with pytest.raises(Exception):
        write_appconfig(db, bad)
    # 模拟"后续无关 writer 的 commit"——若无 rollback,这里会冲刷孤儿 DELETE+partial(A),
    # 使 model_defs 变成 ["A"] 而非 ["keep"]。
    db.conn.commit()
    names = [r["name"] for r in db.conn.execute("SELECT name FROM model_defs")]
    assert names == ["keep"]  # rollback 生效:原模型世界完好,无 partial 残留


def test_read_appconfig_round_trips_back(tmp_path):
    db = open_db(tmp_path / "t.db")
    original = _sample_cfg()
    write_appconfig(db, original)

    out = read_appconfig(db)
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
    assert scheme.command.exe == "q"
    assert scheme.command.args == ("echo", "hi")


def test_read_appconfig_empty_db_returns_defaults(tmp_path):
    db = open_db(tmp_path / "t.db")
    out = read_appconfig(db)
    assert out.models == {}
    assert out.wol is None
    assert out.claude_configs == {}
    assert out.program.host == "0.0.0.0" and out.program.port == 8080
    assert out.program.log_retention_days == 30 and out.program.log_retention_count == 10


def test_config_store_snapshot_and_reload(tmp_path):
    db = open_db(tmp_path / "t.db")
    write_appconfig(
        db,
        AppConfig(
            program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
            models={"M": ModelConfig("M", ("M",), "Chat", 1)},
            wol=None,
            claude_configs={},
        ),
    )
    store = ConfigStore(db)
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
    assert get_setting(db, "log_retention_days") == "30"


def test_initialize_imports_legacy_yaml_when_db_empty(tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "program: {host: 0.0.0.0, port: 8080, alive_time: 60, log_level: INFO}\n"
        "Local-Models:\n"
        "  Qwen3-4B:\n"
        '    aliases: ["Qwen3-4B"]\n'
        "    mode: Chat\n"
        "    port: 10001\n"
        "    RTX4060:\n"
        '      required_devices: ["rtx 4060"]\n'
        '      command: {exe: "q.bat"}\n'
        '      memory_mb: {"rtx 4060": 5120}\n',
        encoding="utf-8",
    )
    db = open_db(tmp_path / "t.db")
    initialize(db, legacy_yaml=yaml_path)
    out = read_appconfig(db)
    assert "Qwen3-4B" in out.models
    assert out.models["Qwen3-4B"].schemes["RTX4060"].required_devices == frozenset({"rtx 4060"})


def test_initialize_skips_import_when_already_initialized(tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "program: {host: 0.0.0.0, port: 8080, alive_time: 60, log_level: INFO}\n"
        "Local-Models:\n  X: {aliases: [x], mode: Chat, port: 1, S: {required_devices: [gpu], command: {exe: a.bat}, memory_mb: {gpu: 1}}}\n",
        encoding="utf-8",
    )
    db = open_db(tmp_path / "t.db")
    initialize(db, legacy_yaml=yaml_path)  # 第一次:导入 X
    # 把模型世界清空(模拟后续手动 DB 状态),再 initialize → 不应重新导入
    with db.write_lock:
        db.conn.execute("DELETE FROM model_defs")
        db.conn.commit()
    initialize(db, legacy_yaml=yaml_path)  # 已 initialized(system_settings 非空)→ 跳过
    assert read_appconfig(db).models == {}


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
    assert get_setting(db, "port") == "8080"  # 默认值不动


def test_initialize_applies_env_after_import(monkeypatch, tmp_path):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "program: {host: 0.0.0.0, port: 8080, alive_time: 60, log_level: INFO}\n", encoding="utf-8"
    )
    monkeypatch.setenv("LLM_MANAGER_PORT", "7000")
    db = open_db(tmp_path / "t.db")
    initialize(db, legacy_yaml=yaml_path)
    assert get_setting(db, "port") == "7000"  # env 覆盖导入值


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
        "      command: {exe: a.bat}\n"
        "      memory_mb: {gpu: 1}\n",
        encoding="utf-8",
    )
    db = open_db(tmp_path / "t.db")
    with pytest.raises(ValueError):
        initialize(db, legacy_yaml=yaml_path)
    # validate 在 write_appconfig 之前 → DB 干净,gate 未翻
    assert is_initialized(db) is False
    assert read_appconfig(db).models == {}


def test_initialize_failed_import_keeps_gate_open_for_recovery(tmp_path, monkeypatch):
    yaml_path = tmp_path / "config.yaml"
    yaml_path.write_text(
        "program: {host: 0.0.0.0, port: 8080, alive_time: 60, log_level: INFO}\n"
        "Local-Models:\n"
        "  M: {aliases: [m], mode: Chat, port: 1, S: {required_devices: [gpu], command: {exe: a.bat}, memory_mb: {gpu: 1}}}\n",
        encoding="utf-8",
    )
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
    assert "M" in read_appconfig(db).models


def test_apply_env_overrides_rejects_non_int_port_without_poisoning_db(monkeypatch, tmp_path):
    db = open_db(tmp_path / "t.db")
    seed_defaults(db)
    monkeypatch.setenv("LLM_MANAGER_PORT", "abc")
    with pytest.raises(ValueError):
        apply_env_overrides(db)
    # 坏 env 在写库前被拒 → DB 未被污染(port 保持默认,不会形成持续 boot-loop)
    assert get_setting(db, "port") == "8080"


def test_apply_env_overrides_rejects_non_int_alive_time(monkeypatch, tmp_path):
    db = open_db(tmp_path / "t.db")
    seed_defaults(db)
    monkeypatch.setenv("LLM_MANAGER_ALIVE_TIME", "soon")
    with pytest.raises(ValueError):
        apply_env_overrides(db)
    assert get_setting(db, "alive_time") == "60"


def test_set_settings_atomic_multi_key_write(tmp_path):
    db = open_db(tmp_path / "t.db")
    set_settings(db, {"host": "127.0.0.1", "port": "9000"})
    assert get_setting(db, "host") == "127.0.0.1"
    assert get_setting(db, "port") == "9000"


def test_set_settings_rolls_back_on_failure(tmp_path):
    db = open_db(tmp_path / "t.db")
    set_setting(db, "port", "8080")
    # 让写入中途失败:monkeypatch _upsert_locked 第二次调用抛
    import llm_manager.data.config_store as cs

    orig = cs._upsert_locked
    calls = {"n": 0}

    def boom(db, k, v):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("disk full")
        return orig(db, k, v)

    cs._upsert_locked = boom
    try:
        with pytest.raises(RuntimeError):
            set_settings(db, {"host": "1.1.1.1", "port": "9999"})
    finally:
        cs._upsert_locked = orig
    # rollback:host(失败前已 stage)被回滚 → None;port(失败处)未写,仍是原值 8080
    assert get_setting(db, "host") is None
    assert get_setting(db, "port") == "8080"


def test_read_appconfig_locked_callable_under_held_lock(tmp_path):
    """_read_appconfig_locked 不取锁 → caller 持 write_lock 调用不死锁(mutate_appconfig 依赖)。"""
    db = open_db(tmp_path / "t.db")
    write_appconfig(db, _sample_cfg())
    with db.write_lock:  # caller 持锁
        cfg = _read_appconfig_locked(db)  # 不应取锁/不应抛
    assert "Qwen3-4B" in cfg.models


def test_write_appconfig_locked_callable_under_held_lock(tmp_path):
    db = open_db(tmp_path / "t.db")
    with db.write_lock:  # caller 持锁
        _write_appconfig_locked(db, _sample_cfg())
        db.conn.commit()  # _write_appconfig_locked 不再自 commit
    assert "Qwen3-4B" in read_appconfig(db).models


def test_mutate_appconfig_applies_fn_and_returns_new_cfg(tmp_path):
    from dataclasses import replace

    db = open_db(tmp_path / "t.db")
    write_appconfig(db, _sample_cfg())  # 有 Qwen3-4B

    def add(cfg):
        m = ModelConfig(
            "New",
            ("new",),
            "Chat",
            7000,
            schemes={"s": Scheme("s", frozenset({"gpu"}), Command(exe="x"), {})},
        )
        return replace(cfg, models={**cfg.models, "New": m})

    new_cfg = mutate_appconfig(db, add)
    assert "New" in new_cfg.models
    assert "New" in read_appconfig(db).models


def test_mutate_appconfig_rolls_back_on_validation_failure(tmp_path):
    from dataclasses import replace

    db = open_db(tmp_path / "t.db")
    write_appconfig(db, _sample_cfg())  # Qwen3-4B aliases 含 "q4"

    # fn 加一个模型,其 alias "q4" 与既有冲突 → validate 失败 → 必须回滚
    def clash(cfg):
        dup = ModelConfig(
            "Dup",
            ("q4",),
            "Chat",
            7000,
            schemes={"s": Scheme("s", frozenset({"gpu"}), Command(exe="x"), {})},
        )
        return replace(cfg, models={**cfg.models, "Dup": dup})

    with pytest.raises(ConfigValidationFailed):
        mutate_appconfig(db, clash)
    out = read_appconfig(db)
    assert "Qwen3-4B" in out.models  # 原配置完好
    assert "Dup" not in out.models  # 回滚:未落 partial


def test_pricing_round_trips_through_config_store(tmp_path):
    from llm_manager.config import Pricing, PricingTier

    db = open_db(tmp_path / "t.db")
    scheme = Scheme(
        config_source="S",
        required_devices=frozenset({"gpu"}),
        command=Command(exe="q"),
        memory_mb={"gpu": 1},
    )
    cfg = AppConfig(
        program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
        models={
            "M": ModelConfig(
                primary_name="M",
                aliases=("M",),
                mode="Chat",
                port=1,
                schemes={"S": scheme},
                pricing=Pricing(
                    pricing_type="tier",
                    hourly_price=2.5,
                    support_cache=True,
                    tiers=(
                        PricingTier(
                            tier_index=1,
                            min_input=0,
                            max_input=32768,
                            input_price=3.0,
                            output_price=9.0,
                            cache_write_price=3.75,
                            cache_read_price=0.3,
                        ),
                    ),
                ),
            )
        },
        wol=None,
        claude_configs={},
    )
    write_appconfig(db, cfg)
    out = read_appconfig(db)
    p = out.models["M"].pricing
    assert p.pricing_type == "tier"
    assert p.hourly_price == 2.5
    assert p.support_cache is True
    assert len(p.tiers) == 1
    t = p.tiers[0]
    assert t.tier_index == 1 and t.input_price == 3.0 and t.output_price == 9.0
    assert t.cache_write_price == 3.75 and t.cache_read_price == 0.3


def test_pricing_survives_unrelated_model_world_rewrite(tmp_path):
    """CASCADE landmine: rewriting the model world must not wipe pricing that round-trips."""
    from dataclasses import replace
    from llm_manager.config import Pricing, PricingTier

    db = open_db(tmp_path / "t.db")
    scheme = Scheme("S", frozenset({"gpu"}), Command(exe="q"), {"gpu": 1})
    priced = ModelConfig(
        "M",
        ("M",),
        "Chat",
        1,
        False,
        {"S": scheme},
        pricing=Pricing(tiers=(PricingTier(tier_index=1, input_price=5.0),)),
    )
    write_appconfig(
        db,
        AppConfig(
            program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
            models={"M": priced},
            wol=None,
            claude_configs={},
        ),
    )
    # mutate program only (triggers full model-world delete+reinsert)
    cfg2 = read_appconfig(db)
    write_appconfig(db, replace(cfg2, program=replace(cfg2.program, port=9999)))
    out = read_appconfig(db)
    assert out.models["M"].pricing.tiers[0].input_price == 5.0  # pricing survived
    assert out.program.port == 9999


def test_retention_keys_round_trip_through_appconfig(tmp_path):
    db = open_db(tmp_path / "t.db")
    write_appconfig(db, _sample_cfg())
    set_setting(db, "log_retention_days", "7")
    set_setting(db, "log_retention_count", "3")
    out = read_appconfig(db)
    assert out.program.log_retention_days == 7
    assert out.program.log_retention_count == 3
    write_appconfig(db, out)  # 全量往返:快照含 retention → 写回不丢
    out2 = read_appconfig(db)
    assert (out2.program.log_retention_days, out2.program.log_retention_count) == (7, 3)


def test_retention_bad_values_fall_back_to_defaults(tmp_path):
    db = open_db(tmp_path / "t.db")
    write_appconfig(db, _sample_cfg())
    set_setting(db, "log_retention_days", "abc")
    out = read_appconfig(db)
    assert out.program.log_retention_days == 30  # 回退默认
    assert out.program.log_retention_count == 10


def test_mutate_appconfig_post_write_runs_before_commit(tmp_path):
    """post_write 在 _write_appconfig_locked 之后、commit 之前执行,能读到本次未提交的写入。"""
    db = open_db(tmp_path / "t.db")
    write_appconfig(db, _sample_cfg())
    seen = {}

    def post(d, old_cfg, new_cfg):
        seen["called"] = True
        names = {r["name"] for r in d.conn.execute("SELECT name FROM model_defs")}
        seen["has_sample"] = bool(names)  # _write_appconfig_locked 已写、尚未 commit

    mutate_appconfig(db, lambda c: c, post_write=post)
    assert seen["called"] is True
    assert seen["has_sample"] is True


def test_mutate_appconfig_post_write_failure_rolls_back(tmp_path):
    """post_write 抛异常 → 整事务回滚(config 写也不留)。"""
    db = open_db(tmp_path / "t.db")
    write_appconfig(db, _sample_cfg())
    before = read_appconfig(db).program.port

    def boom(d, old_cfg, new_cfg):
        raise RuntimeError("boom")

    import pytest

    with pytest.raises(RuntimeError):
        mutate_appconfig(
            db,
            lambda c: replace(c, program=replace(c.program, port=before + 1)),
            post_write=boom,
        )
    assert read_appconfig(db).program.port == before  # 回滚:port 未变
