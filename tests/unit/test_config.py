from pathlib import Path

from llm_manager.config import (Command, AppConfig, ModelConfig, ProgramConfig, Scheme,
                                load, ModelMode, resolve_alias, select_adaptive, validate)


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
      command: {exe: "q.bat"}
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
    assert scheme.command.exe == "q.bat"


def test_model_mode_values():
    assert {m.value for m in ModelMode} == {"Chat", "Embedding", "Reranker"}


def test_validate_flags_port_and_alias_clash_and_bad_mode():
    cfg = AppConfig(
        program=ProgramConfig(host="0.0.0.0", port=8080, alive_time=60, log_level="INFO"),
        models={
            "A": ModelConfig("A", ("x",), "Chat", 1, False, {}),
            "B": ModelConfig("B", ("x",), "Embedding", 1, False, {}),
            "C": ModelConfig("C", ("y",), "Bogus", 2, False, {}),
            "D": ModelConfig("D", ("z",), "Chat", 3, False, {}),
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
        models={"A": ModelConfig("A", ("a",), "Chat", 1, False, {"S": Scheme("S", frozenset({"gpu"}), Command(exe="a.bat"), {"gpu": 1})})},
        wol=None, claude_configs={},
    )
    assert validate(cfg) == []


def test_select_adaptive_first_subset_wins():
    s_gpu = Scheme("GPU", frozenset({"gpu"}), Command(exe="g.bat"), {"gpu": 1})
    s_apu = Scheme("APU", frozenset({"apu"}), Command(exe="a.bat"), {"apu": 1})
    m = ModelConfig("M", ("M",), "Chat", 1, False, {"GPU": s_gpu, "APU": s_apu})
    assert select_adaptive(m, {"gpu"}).config_source == "GPU"
    assert select_adaptive(m, {"apu"}).config_source == "APU"
    assert select_adaptive(m, set()) is None


def test_resolve_alias_to_primary():
    cfg = AppConfig(
        program=ProgramConfig(host="0.0.0.0", port=8080, alive_time=60, log_level="INFO"),
        models={"Qwen3-4B": ModelConfig("Qwen3-4B", ("Qwen3-4B", "q4"), "Chat", 1)},
        wol=None, claude_configs={},
    )
    assert resolve_alias(cfg, "q4") == "Qwen3-4B"
    assert resolve_alias(cfg, "Qwen3-4B") == "Qwen3-4B"
    try:
        resolve_alias(cfg, "nope")
        assert False, "expected KeyError"
    except KeyError:
        pass


def test_referenced_devices_unions_required_and_memory_keys():
    from llm_manager.config import (
        referenced_devices, AppConfig, ProgramConfig, ModelConfig, Scheme, Command,
    )
    s1 = Scheme(config_source="S1", required_devices=frozenset({"rtx 4060", "v100"}),
                command=Command(exe="a.bat"), memory_mb={"rtx 4060": 5120})
    s2 = Scheme(config_source="S2", required_devices=frozenset({"780m"}),
                command=Command(exe="b.bat"), memory_mb={"780m": 2048, "v100": 0})
    m = ModelConfig(primary_name="M", aliases=("m",), mode="Chat", port=1000,
                    schemes={"S1": s1, "S2": s2})
    cfg = AppConfig(program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
                    models={"M": m}, wol=None, claude_configs={})
    assert referenced_devices(cfg) == {"rtx 4060", "v100", "780m"}


def test_referenced_devices_empty_when_no_schemes():
    from llm_manager.config import (
        referenced_devices, AppConfig, ProgramConfig, ModelConfig,
    )
    m = ModelConfig(primary_name="M", aliases=("m",), mode="Chat", port=1000)
    cfg = AppConfig(program=ProgramConfig("0.0.0.0", 8080, 60, "INFO"),
                    models={"M": m}, wol=None, claude_configs={})
    assert referenced_devices(cfg) == set()
