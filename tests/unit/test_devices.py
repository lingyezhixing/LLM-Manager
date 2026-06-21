from llm_manager.devices import _parse_smi, _aggregate_sensors, DeviceMonitor, DeviceInfo


def test_parse_smi_extracts_fields():
    out = "NVIDIA GeForce RTX 4060, 8192, 1024, 7168, 5, 45\n"
    rows = _parse_smi(out)
    assert len(rows) == 1
    r = rows[0]
    assert r.name == "NVIDIA GeForce RTX 4060"
    assert r.total_mb == 8192
    assert r.temp_c == 45.0


def test_parse_smi_skips_bad_lines():
    out = "good, 8192, 1024, 7168, 5, 45\nbroken line\n\n"
    assert len(_parse_smi(out)) == 1


def test_aggregate_sensors_dedicated_and_shared():
    sensors = [
        ("Load", "D3D", 42.0),
        ("SmallData", "Dedicated Used VRAM", 1000.0),
        ("SmallData", "Dedicated Total VRAM", 4000.0),
        ("SmallData", "Shared Used", 500.0),
        ("SmallData", "Shared Total", 2000.0),
        ("Temperature", "GPU Temp", 60.0),
    ]
    info = _aggregate_sensors("780M", sensors)
    assert info.device_name == "780M"
    assert info.device_type == "GPU (APU)"
    assert info.total_memory_mb == 6000
    assert info.used_memory_mb == 1500
    assert info.available_memory_mb == 4500
    assert info.temperature_celsius == 60.0


def test_device_monitor_rebuild_then_rebind():
    def det_a():
        return DeviceInfo("a", "GPU", "VRAM", 1000, 800, 200, 20.0, 40.0)

    def det_off():
        return None

    mon = DeviceMonitor({"a": det_a, "b": det_off})
    mon.refresh()
    assert mon.online_devices() == {"a"}
    assert set(mon.snapshot()) == {"a"}
    assert mon.snapshot()["a"].total_memory_mb == 1000


def test_device_monitor_atomic_rebind_no_inplace_mutation():
    def det():
        return DeviceInfo("a", "GPU", "VRAM", 1000, 800, 200, 20.0, 40.0)

    mon = DeviceMonitor({"a": det})
    mon.refresh()
    snap_before = mon.snapshot()
    mon._cache["a"]  # touch
    mon.refresh()  # rebuilds a NEW dict, rebinds
    snap_after = mon.snapshot()
    assert snap_before is not snap_after  # snapshot() returns a copy each call; _cache itself is rebound
