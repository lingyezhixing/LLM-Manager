from llm_manager.devices import _parse_smi, _aggregate_sensors


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


def test_tokens_splits_alnum():
    from llm_manager.devices import _tokens
    assert _tokens("RTX 4060 Ti") == {"rtx", "4060", "ti"}
    assert _tokens("V100-SXM2") == {"v100", "sxm2"}
    assert _tokens("780M Graphics") == {"780m", "graphics"}
    assert _tokens("") == set()


def _di(name):
    """测试用 DeviceInfo 构造器(仅 device_name 重要,其余置零)。"""
    from llm_manager.devices import DeviceInfo
    return DeviceInfo(name, "GPU", "VRAM", 0, 0, 0, 0.0, None)


def test_match_devices_full_match_keyed_by_config_name():
    from llm_manager.devices import match_devices
    candidates = [
        _di("NVIDIA GeForce RTX 4060 Ti"),
        _di("Tesla V100-SXM2-32GB"),
        _di("AMD Radeon 780M Graphics"),
        _di("CPU"),
    ]
    matched, unmatched = match_devices({"rtx 4060", "v100", "780m", "cpu"}, candidates)
    assert set(matched) == {"rtx 4060", "v100", "780m", "cpu"}
    assert matched["v100"].device_name == "Tesla V100-SXM2-32GB"
    assert unmatched == []


def test_match_devices_no_match_returns_empty_and_unmatched_preserved():
    from llm_manager.devices import match_devices
    candidates = [_di("NVIDIA GeForce RTX 4060")]
    matched, unmatched = match_devices({"rtx 5090"}, candidates)
    assert matched == {}
    assert [c.device_name for c in unmatched] == ["NVIDIA GeForce RTX 4060"]


def test_match_devices_disambiguation_prefers_fewer_extra_tokens():
    # config 'rtx 4060' 同时全子集匹配 'RTX 4060'(多余 2)与 'RTX 4060 Ti'(多余 3)→ 选前者
    from llm_manager.devices import match_devices
    candidates = [_di("NVIDIA GeForce RTX 4060 Ti"), _di("NVIDIA GeForce RTX 4060")]
    matched, _ = match_devices({"rtx 4060"}, candidates)
    assert matched["rtx 4060"].device_name == "NVIDIA GeForce RTX 4060"


def test_match_devices_cpu_token_matches_cpu_candidate():
    from llm_manager.devices import match_devices
    matched, unmatched = match_devices({"cpu"}, [_di("CPU")])
    assert "cpu" in matched
    assert unmatched == []


def test_match_devices_one_candidate_one_name():
    # 重叠 config 名:sorted 先到先得,单候选只配一个
    from llm_manager.devices import match_devices
    matched, unmatched = match_devices({"4060", "rtx 4060"}, [_di("NVIDIA GeForce RTX 4060")])
    assert set(matched) == {"4060"}  # sorted: "4060" < "rtx 4060";后者候选已被占用
    assert unmatched == []


def test_match_devices_requires_full_subset_not_partial():
    # 'rtx 4060' {rtx,4060} 对 'RTX 3090' {rtx,3090} 非 full subset(4060 不在)→ 不匹配
    from llm_manager.devices import match_devices
    matched, unmatched = match_devices({"rtx 4060"}, [_di("NVIDIA GeForce RTX 3090")])
    assert matched == {}
    assert [c.device_name for c in unmatched] == ["NVIDIA GeForce RTX 3090"]


def test_enumerate_nvidia_returns_all_rows_with_raw_names(monkeypatch):
    import llm_manager.devices as dev
    smi = ("NVIDIA GeForce RTX 4060 Laptop GPU, 8188, 1692, 6266, 35, 51\n"
           "Tesla V100-SXM2-32GB, 32768, 0, 32365, 0, 40\n")
    monkeypatch.setattr(dev, "_run_smi", lambda: smi)
    out = dev.enumerate_nvidia()
    assert len(out) == 2
    assert out[0].device_name == "NVIDIA GeForce RTX 4060 Laptop GPU"  # 原始名,非 config 键
    assert out[0].device_type == "GPU" and out[0].memory_type == "VRAM"
    assert out[0].total_memory_mb == 8188 and out[0].available_memory_mb == 6266
    assert out[1].device_name == "Tesla V100-SXM2-32GB" and out[1].total_memory_mb == 32768


def test_enumerate_nvidia_empty_when_no_smi(monkeypatch):
    import llm_manager.devices as dev
    monkeypatch.setattr(dev, "_run_smi", lambda: "")
    assert dev.enumerate_nvidia() == []


def test_lhm_computer_unavailable_returns_none(monkeypatch):
    import llm_manager.devices as dev
    monkeypatch.setattr(dev, "is_lhm_available", lambda: False)
    assert dev._lhm_computer() is None


def test_lhm_computer_init_failure_returns_none(monkeypatch):
    # 初始化抛异常(AddReference 失败等)→ None,不穿透(防 enumerate_cpu 的 try 外调用)
    import sys
    import types
    import llm_manager.devices as dev

    def boom(*a, **k):
        raise RuntimeError("AddReference failed")

    fake_clr = types.ModuleType("clr")
    fake_clr.AddReference = boom
    monkeypatch.setattr(dev, "is_lhm_available", lambda: True)
    monkeypatch.setitem(sys.modules, "clr", fake_clr)
    monkeypatch.setattr(dev, "_LHM_COMPUTER", None)
    assert dev._lhm_computer() is None


def test_enumerate_cpu_basic(monkeypatch):
    import llm_manager.devices as dev

    class _Mem:
        total = 16 * 1024**3
        available = 8 * 1024**3
        used = 8 * 1024**3

    monkeypatch.setattr(dev.psutil, "virtual_memory", lambda: _Mem())
    monkeypatch.setattr(dev.psutil, "cpu_percent", lambda interval=None: 33.0)
    monkeypatch.setattr(dev, "_lhm_cpu_temp", lambda: None)
    out = dev.enumerate_cpu()
    assert len(out) == 1
    info = out[0]
    assert info.device_name == "CPU"
    assert info.device_type == "CPU" and info.memory_type == "RAM"
    assert info.total_memory_mb == 16 * 1024  # 16 GB
    assert info.available_memory_mb == 8 * 1024
    assert info.usage_percentage == 33.0
    assert info.temperature_celsius is None


def test_enumerate_cpu_psutil_failure_degraded(monkeypatch):
    import llm_manager.devices as dev

    def boom():
        raise OSError("psutil broke")

    monkeypatch.setattr(dev.psutil, "virtual_memory", boom)
    out = dev.enumerate_cpu()
    assert len(out) == 1  # 恒 1 元素:降级不抛
    assert out[0].device_name == "CPU"
    assert out[0].total_memory_mb == 0  # 降级零值


def test_lhm_cpu_temp_unavailable_returns_none(monkeypatch):
    import llm_manager.devices as dev
    monkeypatch.setattr(dev, "_lhm_computer", lambda: None)
    assert dev._lhm_cpu_temp() is None


def test_enumerate_lhm_gpus_unavailable_returns_empty(monkeypatch):
    import llm_manager.devices as dev
    monkeypatch.setattr(dev, "_lhm_computer", lambda: None)
    assert dev.enumerate_lhm_gpus() == []


def test_device_monitor_matches_config_names_and_keeps_unmatched():
    from llm_manager.devices import DeviceMonitor, DeviceInfo

    def enum_gpus():
        return [
            DeviceInfo("NVIDIA GeForce RTX 4060", "GPU", "VRAM", 8188, 6266, 1692, 35.0, 51.0),
            DeviceInfo("Tesla V100-SXM2-32GB", "GPU", "VRAM", 32768, 32365, 0, 0.0, 40.0),
        ]

    def enum_cpu():
        return [DeviceInfo("CPU", "CPU", "RAM", 16384, 8192, 8192, 33.0, None)]

    mon = DeviceMonitor([enum_gpus, enum_cpu], lambda: {"rtx 4060", "v100"})
    mon.refresh()
    online = mon.online_devices()
    assert "rtx 4060" in online and "v100" in online  # config 名(已匹配)
    assert "CPU" in online  # 未引用,以实测名保留供展示
    snap = mon.snapshot()
    assert snap["rtx 4060"].total_memory_mb == 8188
    assert snap["v100"].total_memory_mb == 32768


def test_device_monitor_dynamic_referenced_new_config_names_apply_without_restart():
    # WebUI 在线加模型引用新设备名:referenced 动态获取 → 下次 refresh 即生效,无需重启
    from llm_manager.devices import DeviceMonitor, DeviceInfo

    def enum_gpus():
        return [
            DeviceInfo("NVIDIA GeForce RTX 4060", "GPU", "VRAM", 8188, 6266, 1692, 35.0, 51.0),
            DeviceInfo("NVIDIA GeForce GTX 1650", "GPU", "VRAM", 4096, 2000, 2096, 10.0, 45.0),
        ]

    referenced = {"rtx 4060"}
    mon = DeviceMonitor([enum_gpus], lambda: referenced)
    mon.refresh()
    assert "rtx 4060" in mon.online_devices()
    assert "NVIDIA GeForce RTX 4060" not in mon.online_devices()  # 已匹配,不再以实测名出现

    # 模拟在线添加引用 GTX 1650 的模型(旧引用删除)
    referenced = {"gtx 1650"}
    mon.refresh()
    online = mon.online_devices()
    assert "gtx 1650" in online
    assert "rtx 4060" not in online
    assert "NVIDIA GeForce RTX 4060" in online  # 落选者回退为原始检测名(对调度无害,供展示)


def test_device_monitor_unmatched_referenced_is_offline():
    from llm_manager.devices import DeviceMonitor
    mon = DeviceMonitor([lambda: []], lambda: {"rtx 5090"})  # 什么都没枚举到
    mon.refresh()
    assert "rtx 5090" not in mon.online_devices()


def test_device_monitor_enumerator_exception_isolated():
    # 单个枚举器抛异常不影响其他
    from llm_manager.devices import DeviceMonitor, DeviceInfo

    def boom():
        raise RuntimeError("backend broke")

    def ok():
        return [DeviceInfo("CPU", "CPU", "RAM", 0, 0, 0, 0.0, None)]

    mon = DeviceMonitor([boom, ok], lambda: {"cpu"})
    mon.refresh()
    assert "cpu" in mon.online_devices()


def test_new_gpu_model_matches_via_config_only(monkeypatch):
    """加设备零改代码:config 写 'rtx 5090',mock nvidia-smi 返回 5090 行 → 匹配成功,无需改 devices.py。"""
    import llm_manager.devices as dev
    smi = "NVIDIA GeForce RTX 5090, 32768, 1000, 31768, 5, 45\n"
    monkeypatch.setattr(dev, "_run_smi", lambda: smi)
    matched, unmatched = dev.match_devices({"rtx 5090"}, dev.enumerate_nvidia())
    assert "rtx 5090" in matched
    assert matched["rtx 5090"].device_name == "NVIDIA GeForce RTX 5090"
    assert matched["rtx 5090"].total_memory_mb == 32768
    assert unmatched == []
