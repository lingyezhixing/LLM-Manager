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


def test_lhm_max_temp_gpu_only():
    from llm_manager.devices import _lhm_max_temp
    assert _lhm_max_temp(60.0, None) == 60.0


def test_lhm_max_temp_cpu_higher_wins():
    from llm_manager.devices import _lhm_max_temp
    assert _lhm_max_temp(55.0, 72.0) == 72.0  # CPU Tctl/Tdie 更高 → 取 CPU


def test_lhm_max_temp_gpu_higher_wins():
    from llm_manager.devices import _lhm_max_temp
    assert _lhm_max_temp(80.0, 70.0) == 80.0


def test_lhm_max_temp_both_none_returns_none():
    from llm_manager.devices import _lhm_max_temp
    assert _lhm_max_temp(None, None) is None


def test_run_smi_uses_noheader_nounits_format(monkeypatch):
    # 真机验证发现:nvidia-smi 默认输出带 [MiB]/[%] 单位,_parse_smi 的 int() 解析失败 →
    # 必须用 --format=csv,noheader,nounits 让输出纯数字(_parse_smi 纯函数不变)。
    import llm_manager.devices as dev
    captured = {}

    class _R:
        returncode = 0
        stdout = "GPU, 8192, 0, 8192, 0, 40\n"

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _R()

    monkeypatch.setattr(dev.subprocess, "run", fake_run)
    dev._run_smi()
    assert "--format=csv,noheader,nounits" in captured["cmd"], captured["cmd"]


def test_parse_smi_handles_multi_gpu_csv_noheader_nounits():
    # nvidia-smi --query-gpu=... --format=csv,noheader,nounits 的实际多 GPU 输出(纯数字)
    out = ("NVIDIA GeForce RTX 4060 Laptop GPU, 8188, 1692, 6266, 35, 51\n"
           "Tesla V100-SXM2-32GB, 32768, 0, 32365, 0, 40\n")
    rows = _parse_smi(out)
    assert len(rows) == 2
    assert "4060" in rows[0].name.lower() and rows[0].total_mb == 8188
    assert "v100" in rows[1].name.lower() and rows[1].total_mb == 32768


def test_detect_nvidia_finds_multiple_gpus_by_name_token(monkeypatch):
    import llm_manager.devices as dev
    real_smi = ("NVIDIA GeForce RTX 4060 Laptop GPU, 8188, 1692, 6266, 35, 51\n"
                "Tesla V100-SXM2-32GB, 32768, 0, 32365, 0, 40\n")
    monkeypatch.setattr(dev, "_run_smi", lambda: real_smi)
    rtx = dev.detect_nvidia("rtx 4060", "4060")
    assert rtx is not None and rtx.device_name == "rtx 4060" and rtx.total_memory_mb == 8188
    v100 = dev.detect_nvidia("v100", "V100")
    assert v100 is not None and v100.device_name == "v100" and v100.total_memory_mb == 32768


def test_tokens_splits_alnum():
    from llm_manager.devices import _tokens
    assert _tokens("RTX 4060 Ti") == {"rtx", "4060", "ti"}
    assert _tokens("V100-SXM2") == {"v100", "sxm2"}
    assert _tokens("780M Graphics") == {"780m", "graphics"}
    assert _tokens("") == set()


def test_match_score_full_subset_is_one():
    from llm_manager.devices import _match_score
    assert _match_score("rtx 4060", {"nvidia", "geforce", "rtx", "4060", "ti"}) == 1.0


def test_match_score_partial_below_one():
    from llm_manager.devices import _match_score
    assert _match_score("rtx 4060", {"rtx", "3090"}) == 0.5  # only "rtx" of {rtx,4060}


def test_match_score_empty_config_is_zero():
    from llm_manager.devices import _match_score
    assert _match_score("", {"rtx", "4060"}) == 0.0
