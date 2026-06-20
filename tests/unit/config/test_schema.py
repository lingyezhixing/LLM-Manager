import pathlib

import pytest
from pydantic import ValidationError

from llm_manager.config.schema import (
    KNOWN_MODEL_SCALARS,
    ModelConfigYAML,
    ProgramConfig,
    SchemeConfig,
)


def test_program_config_defaults():
    p = ProgramConfig()
    assert p.host == "0.0.0.0"
    assert p.port == 8080
    assert p.alive_time == 0
    assert p.log_level == "INFO"
    assert p.data_dir == pathlib.Path("./data")
    assert p.disable_gpu_monitoring is False


def test_scheme_config_requires_fields():
    with pytest.raises(ValidationError):
        SchemeConfig()  # type: ignore[call-arg]


def test_scheme_config_ok():
    s = SchemeConfig(
        required_devices=["rtx 4060"],
        script_path=pathlib.Path("a.bat"),
        memory_mb={"rtx 4060": 5120},
    )
    assert s.memory_mb["rtx 4060"] == 5120


def test_model_config_yaml_ok():
    m = ModelConfigYAML(
        aliases=["qwen", "q"],
        mode="Chat",
        port=10006,
        schemes={
            "RTX4060": SchemeConfig(
                required_devices=["rtx 4060"],
                script_path=pathlib.Path("a.bat"),
                memory_mb={"rtx 4060": 5120},
            )
        },
    )
    assert m.aliases[0] == "qwen"
    assert m.auto_start is False


def test_known_model_scalars_is_the_single_source():
    assert KNOWN_MODEL_SCALARS == frozenset({"aliases", "mode", "port", "auto_start"})
