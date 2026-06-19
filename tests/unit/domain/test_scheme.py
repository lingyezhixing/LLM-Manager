import pathlib

from llm_manager.domain.device import DeviceName
from llm_manager.domain.scheme import AdaptiveScheme


def test_adaptive_scheme_fields():
    s = AdaptiveScheme(
        config_source="RTX4060",
        required_devices=frozenset({DeviceName("rtx 4060")}),
        script_path=pathlib.Path("Model_startup_script/qwen.bat"),
        memory_mb={DeviceName("rtx 4060"): 5120},
    )
    assert s.config_source == "RTX4060"
    assert DeviceName("rtx 4060") in s.required_devices
    assert s.memory_mb[DeviceName("rtx 4060")] == 5120
