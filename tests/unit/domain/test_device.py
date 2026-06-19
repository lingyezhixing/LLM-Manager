from llm_manager.domain.device import DeviceInfo, DeviceName


def test_device_name_is_str_subtype():
    name = DeviceName("rtx 4060")
    assert isinstance(name, str)
    assert name == "rtx 4060"


def test_device_info_is_frozen():
    info = DeviceInfo(
        device_name=DeviceName("v100"),
        device_type="GPU",
        memory_type="VRAM",
        total_memory_mb=32510,
        available_memory_mb=512,
        used_memory_mb=31998,
        usage_percentage=98.4,
        temperature_celsius=71.0,
    )
    assert info.temperature_celsius == 71.0
    import dataclasses

    try:
        info.usage_percentage = 0.0  # type: ignore[misc]
    except dataclasses.FrozenInstanceError:
        return
    raise AssertionError("DeviceInfo must be frozen")
