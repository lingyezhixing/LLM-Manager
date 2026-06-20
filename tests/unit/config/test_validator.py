from llm_manager.config.schema import (
    AppConfig,
    ModelConfigYAML,
    ProgramConfig,
    SchemeConfig,
)
from llm_manager.config.validator import validate


def _ok_cfg() -> AppConfig:
    return AppConfig(
        program=ProgramConfig(),
        models={
            "A": ModelConfigYAML(
                aliases=["A", "a"], mode="Chat", port=1,
                schemes={"X": SchemeConfig(required_devices=["v100"],
                                           script_path="a.bat", memory_mb={"v100": 1})},
            )
        },
    )


def test_valid_config_has_no_errors():
    assert validate(_ok_cfg()) == []


def test_duplicate_port_flagged():
    cfg = _ok_cfg()
    cfg.models["B"] = ModelConfigYAML(
        aliases=["B"], mode="Chat", port=1,
        schemes={"X": SchemeConfig(required_devices=["v100"], script_path="b.bat",
                                   memory_mb={"v100": 1})},
    )
    errs = validate(cfg)
    assert any("port" in e.lower() for e in errs)


def test_unsupported_mode_flagged():
    # "Vision" is not a ModelMode value -> not a supported mode.
    cfg = _ok_cfg()
    cfg.models["A"].mode = "Vision"
    errs = validate(cfg)
    assert any("mode" in e.lower() for e in errs)


def test_duplicate_device_name_case_flagged():
    cfg = AppConfig(
        program=ProgramConfig(),
        models={
            "A": ModelConfigYAML(
                aliases=["A"], mode="Chat", port=1,
                schemes={"X": SchemeConfig(required_devices=["V100"],
                                           script_path="a.bat", memory_mb={"V100": 1})},
            ),
            "B": ModelConfigYAML(
                aliases=["B"], mode="Base", port=2,
                schemes={"Y": SchemeConfig(required_devices=["v100"],
                                           script_path="b.bat", memory_mb={"v100": 1})},
            ),
        },
    )
    errs = validate(cfg)
    assert any("device" in e.lower() for e in errs)
