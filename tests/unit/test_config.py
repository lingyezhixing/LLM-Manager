from pathlib import Path

from llm_manager.config import load, ModelMode, Scheme, validate, select_adaptive, resolve_alias, ModelConfig, AppConfig, ProgramConfig


def _write_cfg(tmp_path: Path, body: str) -> Path:
    p = tmp_path / "config.yaml"
    p.write_text(body, encoding="utf-8")
    return p


def test_load_parses_models_and_normalizes_device_names(tmp_path):
    cfg_path = _write_cfg(tmp_path, """
program: {host: 0.0.0.0, port: 8080, alive_time: 60, log_level: INFO}
Local-Models:
  Qwen3-4B:
    aliases: ["Qwen3-4B"]
    mode: Chat
    port: 10001
    RTX4060:
      required_devices: ["RTX 4060"]
      script_path: "Model_startup_script/q.bat"
      memory_mb: {"RTX 4060": 5120}
""")
    cfg = load(cfg_path)
    m = cfg.models["Qwen3-4B"]
    assert m.port == 10001
    assert m.mode == "Chat"
    assert "Qwen3-4B" in m.aliases
    scheme = m.schemes["RTX4060"]
    assert isinstance(scheme, Scheme)
    assert scheme.required_devices == frozenset({"rtx 4060"})
    assert scheme.memory_mb == {"rtx 4060": 5120}
    assert scheme.script_path == Path("Model_startup_script/q.bat")


def test_model_mode_values():
    assert {m.value for m in ModelMode} == {"Chat", "Base", "Embedding", "Reranker"}


def test_validate_flags_port_and_alias_clash_and_bad_mode():
    cfg = AppConfig(
        program=ProgramConfig(host="0.0.0.0", port=8080, alive_time=60, log_level="INFO"),
        models={
            "A": ModelConfig("A", frozenset({"x"}), "Chat", 1, False, {}),
            "B": ModelConfig("B", frozenset({"x"}), "Base", 1, False, {}),
            "C": ModelConfig("C", frozenset({"y"}), "Bogus", 2, False, {}),
            "D": ModelConfig("D", frozenset({"z"}), "Chat", 3, False, {}),
        },
        wol=None, claude_configs={},
    )
    errs = validate(cfg)
    assert any("Port 1 shared" in e for e in errs)
    assert any("Alias 'x' shared" in e for e in errs)
    assert any("mode 'Bogus'" in e for e in errs)
    assert any("no device scheme" in e for e in errs)


def test_validate_passes_clean_config():
    cfg = AppConfig(
        program=ProgramConfig(host="0.0.0.0", port=8080, alive_time=60, log_level="INFO"),
        models={"A": ModelConfig("A", frozenset({"a"}), "Chat", 1, False, {"S": Scheme("S", frozenset({"gpu"}), Path("a.bat"), {"gpu": 1})})},
        wol=None, claude_configs={},
    )
    assert validate(cfg) == []


def test_select_adaptive_first_subset_wins():
    s_gpu = Scheme("GPU", frozenset({"gpu"}), Path("g.bat"), {"gpu": 1})
    s_apu = Scheme("APU", frozenset({"apu"}), Path("a.bat"), {"apu": 1})
    m = ModelConfig("M", frozenset({"M"}), "Chat", 1, False, {"GPU": s_gpu, "APU": s_apu})
    assert select_adaptive(m, {"gpu"}).config_source == "GPU"
    assert select_adaptive(m, {"apu"}).config_source == "APU"
    assert select_adaptive(m, set()) is None


def test_resolve_alias_to_primary():
    cfg = AppConfig(
        program=ProgramConfig(host="0.0.0.0", port=8080, alive_time=60, log_level="INFO"),
        models={"Qwen3-4B": ModelConfig("Qwen3-4B", frozenset({"Qwen3-4B", "q4"}), "Chat", 1)},
        wol=None, claude_configs={},
    )
    assert resolve_alias(cfg, "q4") == "Qwen3-4B"
    assert resolve_alias(cfg, "Qwen3-4B") == "Qwen3-4B"
    try:
        resolve_alias(cfg, "nope"); assert False, "expected KeyError"
    except KeyError:
        pass
