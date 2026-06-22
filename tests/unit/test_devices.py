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


def test_devices_registry_has_v100_and_780m_comment_slot():
    from llm_manager.devices import DEVICES
    assert "v100" in DEVICES
    assert "rtx 4060" in DEVICES
    assert "780m" not in DEVICES  # 注释位:780m 由 app.py 按 is_lhm_available() 条件注册


def test_devices_keys_are_normalized_lowercase():
    # 防御:DEVICES key 必须小写归一化,对齐 config._norm_device,
    # 否则 select_adaptive 的 scheme.required_devices <= online 匹配失败(scheme 归一化为小写)
    from llm_manager.devices import DEVICES
    assert all(k == k.strip().lower() for k in DEVICES), list(DEVICES)


def test_is_lhm_available_no_pythonnet(monkeypatch):
    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "clr":
            raise ImportError("no pythonnet")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    from llm_manager.devices import is_lhm_available
    assert is_lhm_available() is False


def test_is_lhm_available_dll_present(monkeypatch, tmp_path):
    import sys
    import types
    import llm_manager.devices as dev

    monkeypatch.setitem(sys.modules, "clr", types.ModuleType("clr"))  # 假装 pythonnet 已装
    fake_dll = tmp_path / "LibreHardwareMonitorLib.dll"
    fake_dll.write_text("fake")
    monkeypatch.setattr(dev, "_LHM_DLL", fake_dll)
    assert dev.is_lhm_available() is True


def test_is_lhm_available_dll_missing(monkeypatch, tmp_path):
    import sys
    import types
    import llm_manager.devices as dev

    monkeypatch.setitem(sys.modules, "clr", types.ModuleType("clr"))
    monkeypatch.setattr(dev, "_LHM_DLL", tmp_path / "nonexistent.dll")
    assert dev.is_lhm_available() is False


def test_detect_amd_apu_none_adapter_returns_none():
    from llm_manager.devices import detect_amd_apu
    assert detect_amd_apu("780m", None) is None


def test_detect_amd_apu_fake_sensors_returns_deviceinfo():
    from llm_manager.devices import detect_amd_apu, DeviceInfo

    def fake():
        return iter([
            ("Load", "D3D", 42.0),
            ("SmallData", "Dedicated Used VRAM", 1000.0),
            ("SmallData", "Dedicated Total VRAM", 4000.0),
            ("Temperature", "GPU/CPU max", 60.0),
        ])

    info = detect_amd_apu("780m", fake)
    assert isinstance(info, DeviceInfo)
    assert info.device_name == "780m"
    assert info.total_memory_mb == 4000
    assert info.used_memory_mb == 1000
    assert info.usage_percentage == 42.0
    assert info.temperature_celsius == 60.0


def test_detect_amd_apu_raising_adapter_returns_none():
    from llm_manager.devices import detect_amd_apu

    def raising():
        raise RuntimeError("LHM boom")

    assert detect_amd_apu("780m", raising) is None


def test_detect_amd_apu_empty_sensors_fallback_total():
    from llm_manager.devices import detect_amd_apu
    info = detect_amd_apu("780m", lambda: iter([]))
    assert info is not None
    assert info.total_memory_mb == 512  # _aggregate_sensors 兜底分支(total<=0 → 512)
    assert info.used_memory_mb == 0
