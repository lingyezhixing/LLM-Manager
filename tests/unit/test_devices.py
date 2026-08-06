from llm_manager.devices.adapters import _parse_smi, _aggregate_sensors


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
    from llm_manager.devices import adapters as ad

    monkeypatch.setitem(sys.modules, "clr", types.ModuleType("clr"))  # 假装 pythonnet 已装
    fake_dll = tmp_path / "LibreHardwareMonitorLib.dll"
    fake_dll.write_text("fake")
    monkeypatch.setattr(ad, "_LHM_DLL", fake_dll)
    assert ad.is_lhm_available() is True


def test_is_lhm_available_dll_missing(monkeypatch, tmp_path):
    import sys
    import types
    from llm_manager.devices import adapters as ad

    monkeypatch.setitem(sys.modules, "clr", types.ModuleType("clr"))
    monkeypatch.setattr(ad, "_LHM_DLL", tmp_path / "nonexistent.dll")
    assert ad.is_lhm_available() is False


def test_run_smi_uses_noheader_nounits_format(monkeypatch):
    # 真机验证发现:nvidia-smi 默认输出带 [MiB]/[%] 单位,_parse_smi 的 int() 解析失败 →
    # 必须用 --format=csv,noheader,nounits 让输出纯数字(_parse_smi 纯函数不变)。
    from llm_manager.devices import adapters as ad
    captured = {}

    class _R:
        returncode = 0
        stdout = "GPU, 8192, 0, 8192, 0, 40\n"

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return _R()

    monkeypatch.setattr(ad.subprocess, "run", fake_run)
    ad._run_smi()
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
    from llm_manager.devices import adapters as ad
    smi = ("NVIDIA GeForce RTX 4060 Laptop GPU, 8188, 1692, 6266, 35, 51\n"
           "Tesla V100-SXM2-32GB, 32768, 0, 32365, 0, 40\n")
    monkeypatch.setattr(ad, "_run_smi", lambda: smi)
    out = ad.NvidiaAdapter().enumerate()
    assert len(out) == 2
    assert out[0].device_name == "NVIDIA GeForce RTX 4060 Laptop GPU"  # 原始名,非 config 键
    assert out[0].device_type == "GPU" and out[0].memory_type == "VRAM"
    assert out[0].total_memory_mb == 8188 and out[0].available_memory_mb == 6266
    assert out[1].device_name == "Tesla V100-SXM2-32GB" and out[1].total_memory_mb == 32768


def test_enumerate_nvidia_empty_when_no_smi(monkeypatch):
    from llm_manager.devices import adapters as ad
    monkeypatch.setattr(ad, "_run_smi", lambda: "")
    assert ad.NvidiaAdapter().enumerate() == []


def test_lhm_computer_unavailable_returns_none(monkeypatch):
    from llm_manager.devices import adapters as ad
    monkeypatch.setattr(ad, "is_lhm_available", lambda: False)
    assert ad._lhm_computer() is None


def test_lhm_computer_init_failure_returns_none(monkeypatch):
    # 初始化抛异常(AddReference 失败等)→ None,不穿透(防 CpuAdapter.enumerate 的 try 外调用)
    import sys
    import types
    from llm_manager.devices import adapters as ad

    def boom(*a, **k):
        raise RuntimeError("AddReference failed")

    fake_clr = types.ModuleType("clr")
    fake_clr.AddReference = boom
    monkeypatch.setattr(ad, "is_lhm_available", lambda: True)
    monkeypatch.setitem(sys.modules, "clr", fake_clr)
    monkeypatch.setattr(ad, "_LHM_COMPUTER", None)
    assert ad._lhm_computer() is None


def test_enumerate_cpu_basic(monkeypatch):
    from llm_manager.devices import adapters as ad

    class _Mem:
        total = 16 * 1024**3
        available = 8 * 1024**3
        used = 8 * 1024**3

    monkeypatch.setattr(ad.psutil, "virtual_memory", lambda: _Mem())
    monkeypatch.setattr(ad.psutil, "cpu_percent", lambda interval=None: 33.0)
    monkeypatch.setattr(ad, "_lhm_cpu_temp", lambda: None)
    out = ad.CpuAdapter().enumerate()
    assert len(out) == 1
    info = out[0]
    assert info.device_name == "CPU"
    assert info.device_type == "CPU" and info.memory_type == "RAM"
    assert info.total_memory_mb == 16 * 1024  # 16 GB
    assert info.available_memory_mb == 8 * 1024
    assert info.usage_percentage == 33.0
    assert info.temperature_celsius is None


def test_enumerate_cpu_psutil_failure_degraded(monkeypatch):
    from llm_manager.devices import adapters as ad

    def boom():
        raise OSError("psutil broke")

    monkeypatch.setattr(ad.psutil, "virtual_memory", boom)
    out = ad.CpuAdapter().enumerate()
    assert len(out) == 1  # 恒 1 元素:降级不抛
    assert out[0].device_name == "CPU"
    assert out[0].total_memory_mb == 0  # 降级零值


def test_lhm_cpu_temp_unavailable_returns_none(monkeypatch):
    from llm_manager.devices import adapters as ad
    monkeypatch.setattr(ad, "_lhm_computer", lambda: None)
    assert ad._lhm_cpu_temp() is None


def test_enumerate_lhm_gpus_unavailable_returns_empty(monkeypatch):
    from llm_manager.devices import adapters as ad
    monkeypatch.setattr(ad, "_lhm_computer", lambda: None)
    assert ad.LhmAdapter().enumerate() == []


def test_device_monitor_matches_config_names_and_keeps_unmatched():
    from llm_manager.devices import DeviceMonitor, DeviceInfo

    def enum_gpus():
        return [
            DeviceInfo("NVIDIA GeForce RTX 4060", "GPU", "VRAM", 8188, 6266, 1692, 35.0, 51.0),
            DeviceInfo("Tesla V100-SXM2-32GB", "GPU", "VRAM", 32768, 32365, 0, 0.0, 40.0),
        ]

    def enum_cpu():
        return [DeviceInfo("CPU", "CPU", "RAM", 16384, 8192, 8192, 33.0, None)]

    mon = DeviceMonitor([_FakeAdapter(enum_gpus()), _FakeAdapter(enum_cpu())], lambda: {"rtx 4060", "v100"})
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
    mon = DeviceMonitor([_FakeAdapter(enum_gpus())], lambda: referenced)
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
    mon = DeviceMonitor([_FakeAdapter([])], lambda: {"rtx 5090"})  # 什么都没枚举到
    mon.refresh()
    assert "rtx 5090" not in mon.online_devices()


def test_device_monitor_enumerator_exception_isolated():
    # 单个适配器抛异常不影响其他
    from llm_manager.devices import DeviceMonitor, DeviceInfo

    class _BoomAdapter:
        def enumerate(self):
            raise RuntimeError("backend broke")

    ok = DeviceInfo("CPU", "CPU", "RAM", 0, 0, 0, 0.0, None)
    mon = DeviceMonitor([_BoomAdapter(), _FakeAdapter([ok])], lambda: {"cpu"})
    mon.refresh()
    assert "cpu" in mon.online_devices()


class _FakeAdapter:
    def __init__(self, devices):
        self._devices = devices

    def enumerate(self):
        return self._devices


def test_build_adapters_linux_full(monkeypatch):
    from llm_manager.devices import build_adapters
    monkeypatch.setattr("llm_manager.devices.os.name", "posix")
    monkeypatch.setattr("llm_manager.devices.shutil.which", lambda _: "/usr/bin/nvidia-smi")
    monkeypatch.setattr("llm_manager.devices.Path.is_dir", lambda self: True)
    ads = build_adapters()
    assert {type(a).__name__ for a in ads} == {"CpuAdapter", "NvidiaAdapter", "IntelLinuxAdapter", "AmdLinuxAdapter"}


def test_build_adapters_windows(monkeypatch):
    from llm_manager.devices import build_adapters
    monkeypatch.setattr("llm_manager.devices.os.name", "nt")
    monkeypatch.setattr("llm_manager.devices.shutil.which", lambda _: None)
    monkeypatch.setattr("llm_manager.devices.is_lhm_available", lambda: True)
    ads = build_adapters()
    assert {type(a).__name__ for a in ads} == {"CpuAdapter", "LhmAdapter"}


def test_build_adapters_minimal(monkeypatch):
    from llm_manager.devices import build_adapters
    monkeypatch.setattr("llm_manager.devices.os.name", "nt")
    monkeypatch.setattr("llm_manager.devices.shutil.which", lambda _: None)
    monkeypatch.setattr("llm_manager.devices.is_lhm_available", lambda: False)
    ads = build_adapters()
    assert [type(a).__name__ for a in ads] == ["CpuAdapter"]


def test_build_adapters_posix_without_sysfs(monkeypatch):
    # posix + nvidia-smi 在 PATH + /sys/class/drm 不存在 → Linux 双 GPU 适配器不注册
    from llm_manager.devices import build_adapters
    monkeypatch.setattr("llm_manager.devices.os.name", "posix")
    monkeypatch.setattr("llm_manager.devices.shutil.which", lambda _: "/usr/bin/nvidia-smi")
    monkeypatch.setattr("llm_manager.devices.Path.is_dir", lambda self: False)
    ads = build_adapters()
    assert {type(a).__name__ for a in ads} == {"CpuAdapter", "NvidiaAdapter"}


def test_build_adapters_windows_with_nvidia(monkeypatch):
    # nt + nvidia-smi 在 PATH + LHM 不可用 → Cpu + Nvidia(Windows 无 sysfs 分支)
    from llm_manager.devices import build_adapters
    monkeypatch.setattr("llm_manager.devices.os.name", "nt")
    monkeypatch.setattr("llm_manager.devices.shutil.which", lambda _: "C:\\nvidia-smi.exe")
    monkeypatch.setattr("llm_manager.devices.is_lhm_available", lambda: False)
    ads = build_adapters()
    assert {type(a).__name__ for a in ads} == {"CpuAdapter", "NvidiaAdapter"}


def test_new_gpu_model_matches_via_config_only(monkeypatch):
    """加设备零改代码:config 写 'rtx 5090',mock nvidia-smi 返回 5090 行 → 匹配成功,无需改 devices.py。"""
    import llm_manager.devices as dev
    from llm_manager.devices import adapters as ad
    smi = "NVIDIA GeForce RTX 5090, 32768, 1000, 31768, 5, 45\n"
    monkeypatch.setattr(ad, "_run_smi", lambda: smi)
    matched, unmatched = dev.match_devices({"rtx 5090"}, ad.NvidiaAdapter().enumerate())
    assert "rtx 5090" in matched
    assert matched["rtx 5090"].device_name == "NVIDIA GeForce RTX 5090"
    assert matched["rtx 5090"].total_memory_mb == 32768
    assert unmatched == []


# ==================== Intel iGPU(i915 + intel_gpu_top)====================


def _make_i915_sysfs(tmp_path, pci_id="8086:46d1"):
    """假 /sys/class/drm 树:card0 = i915 设备;card1 = amdgpu;card0-DP-1 = connector。"""
    drm = tmp_path / "sys" / "class" / "drm"
    card0 = drm / "card0" / "device"
    card0.mkdir(parents=True)
    card0.joinpath("uevent").write_text(f"DRIVER=i915\nPCI_ID={pci_id}\n")
    card1 = drm / "card1" / "device"
    card1.mkdir(parents=True)
    card1.joinpath("uevent").write_text("DRIVER=amdgpu\nPCI_ID=1002:15fe\n")
    drm.joinpath("card0-DP-1").mkdir(parents=True)
    return drm


def test_intel_adapter_metrics_from_gpu_top(monkeypatch, tmp_path):
    from llm_manager.devices import adapters as ad

    monkeypatch.setattr(ad.os, "name", "posix")
    monkeypatch.setattr(ad, "_DRM_CLASS", _make_i915_sysfs(tmp_path))
    # 两帧:初始化帧(period 0.035ms,应跳过)+ 采样帧(1000ms)
    sample = ('[\n{"period": {"duration": 0.035, "unit": "ms"}, "engines": {"Render/3D": {"busy": 0.0}}}\n,'
              '{"period": {"duration": 1000.34, "unit": "ms"}, "frequency": {"actual": 2400.0, "requested": 2400.0},'
              ' "engines": {"Render/3D": {"busy": 12.5}, "Video": {"busy": 5.0}},'
              ' "power": {"GPU": 3.5, "Package": 2.4}}\n]')
    monkeypatch.setattr(ad, "_run_intel_gpu_top", lambda: sample)
    monkeypatch.setattr(ad.psutil, "virtual_memory", lambda: type("M", (), {"total": 16 * 1024**3, "available": 8 * 1024**3, "used": 8 * 1024**3})())
    out = ad.IntelLinuxAdapter().enumerate()
    assert len(out) == 1  # card0 命中;card1(amdgpu)与 connector 跳过
    info = out[0]
    assert info.device_name == "Intel UHD Graphics (Alder Lake-N)"
    assert info.device_type == "GPU (iGPU)" and info.memory_type == "Shared RAM"
    assert info.usage_percentage == 12.5      # engines busy 取 max
    assert info.freq_mhz == 2400.0
    assert info.power_watts == 3.5
    assert info.temperature_celsius is None   # N100 平台无温度传感器


def test_intel_adapter_gpu_top_failure_degraded(monkeypatch, tmp_path):
    from llm_manager.devices import adapters as ad

    monkeypatch.setattr(ad.os, "name", "posix")
    monkeypatch.setattr(ad, "_DRM_CLASS", _make_i915_sysfs(tmp_path))
    monkeypatch.setattr(ad, "_run_intel_gpu_top", lambda: None)  # 工具缺失/失败
    out = ad.IntelLinuxAdapter().enumerate()
    assert len(out) == 1  # 识别与指标解耦:设备照常出现
    assert out[0].usage_percentage == 0.0
    assert out[0].freq_mhz is None and out[0].power_watts is None


def test_intel_adapter_no_i915_returns_empty(monkeypatch, tmp_path):
    from llm_manager.devices import adapters as ad

    monkeypatch.setattr(ad.os, "name", "posix")
    drm = tmp_path / "sys" / "class" / "drm"
    card1 = drm / "card1" / "device"
    card1.mkdir(parents=True)
    card1.joinpath("uevent").write_text("DRIVER=amdgpu\n")
    monkeypatch.setattr(ad, "_DRM_CLASS", drm)
    assert ad.IntelLinuxAdapter().enumerate() == []


def test_intel_adapter_windows_and_missing_sysfs(monkeypatch, tmp_path):
    from llm_manager.devices import adapters as ad

    monkeypatch.setattr(ad, "_DRM_CLASS", _make_i915_sysfs(tmp_path))
    monkeypatch.setattr(ad.os, "name", "nt")
    assert ad.IntelLinuxAdapter().enumerate() == []

    monkeypatch.setattr(ad.os, "name", "posix")
    monkeypatch.setattr(ad, "_DRM_CLASS", tmp_path / "nonexistent")
    assert ad.IntelLinuxAdapter().enumerate() == []


def test_run_intel_gpu_top_timeout_124_still_returns_stdout(monkeypatch):
    # GNU coreutils timeout 杀子进程必然返回 124 → 124 属预期,截断 stdout 照收(解析器容忍)
    from llm_manager.devices import adapters as ad

    class _R:
        returncode = 124
        stdout = '[\n{"period": {"duration": 1000.0, "unit": "ms"}}\n]'

    monkeypatch.setattr(ad.shutil, "which", lambda _: "/usr/bin/intel_gpu_top")
    monkeypatch.setattr(ad.subprocess, "run", lambda *a, **k: _R())
    assert ad._run_intel_gpu_top() == _R.stdout


def test_run_intel_gpu_top_real_failure_returns_none(monkeypatch):
    # 真失败(工具自身报错)≠ timeout:returncode=1 → None,指标降级
    from llm_manager.devices import adapters as ad

    class _R:
        returncode = 1
        stdout = ""

    monkeypatch.setattr(ad.shutil, "which", lambda _: "/usr/bin/intel_gpu_top")
    monkeypatch.setattr(ad.subprocess, "run", lambda *a, **k: _R())
    assert ad._run_intel_gpu_top() is None


def test_parse_intel_gpu_top_skips_init_frame_and_takes_last(monkeypatch):
    from llm_manager.devices.adapters import _parse_intel_gpu_top

    sample = ('[\n{"period": {"duration": 0.035, "unit": "ms"}, "engines": {"Render/3D": {"busy": 99.0}}}\n,'
              '{"period": {"duration": 1000.0, "unit": "ms"}, "frequency": {"actual": 1500.0},'
              ' "engines": {"Render/3D": {"busy": 10.0}, "Blitter": {"busy": 3.0}}, "power": {"GPU": 1.2}}\n,'
              '{"period": {"duration": 1000.0, "unit": "ms"}, "frequency": {"actual": 1800.0},'
              ' "engines": {"Render/3D": {"busy": 25.0}, "Video": {"busy": 5.0}}, "power": {"GPU": 2.2}}\n]')
    m = _parse_intel_gpu_top(sample)
    # 初始化帧跳过;两个有效帧 → 取最后帧(1800/25/2.2 胜出,取第一帧的 bug 在此暴露)
    assert m == {"busy_pct": 25.0, "freq_mhz": 1800.0, "power_watts": 2.2}


def test_parse_intel_gpu_top_pretty_multiline_real_format(monkeypatch):
    # 真机实测:intel_gpu_top -J 输出为 pretty 多行格式(每帧跨 ~20 行,字段逐行)
    from llm_manager.devices.adapters import _parse_intel_gpu_top

    sample = ('[\n'
              '{\n'
              '\t"period": {\n'
              '\t\t"duration": 0.035112,\n'
              '\t\t"unit": "ms"\n'
              '\t},\n'
              '\t"engines": {\n'
              '\t\t"Render/3D": {\n'
              '\t\t\t"busy": 0.000000,\n'
              '\t\t\t"sema": 0.000000,\n'
              '\t\t\t"wait": 0.000000,\n'
              '\t\t\t"unit": "%"\n'
              '\t\t}\n'
              '\t}\n'
              '},\n'
              '{\n'
              '\t"period": {\n'
              '\t\t"duration": 1000.342406,\n'
              '\t\t"unit": "ms"\n'
              '\t},\n'
              '\t"frequency": {\n'
              '\t\t"requested": 2400.000000,\n'
              '\t\t"actual": 2400.000000,\n'
              '\t\t"unit": "MHz"\n'
              '\t},\n'
              '\t"engines": {\n'
              '\t\t"Render/3D": {\n'
              '\t\t\t"busy": 12.500000,\n'
              '\t\t\t"sema": 0.000000,\n'
              '\t\t\t"wait": 0.000000,\n'
              '\t\t\t"unit": "%"\n'
              '\t\t},\n'
              '\t\t"Video": {\n'
              '\t\t\t"busy": 5.000000,\n'
              '\t\t\t"sema": 0.000000,\n'
              '\t\t\t"wait": 0.000000,\n'
              '\t\t\t"unit": "%"\n'
              '\t\t}\n'
              '\t},\n'
              '\t"power": {\n'
              '\t\t"GPU": 3.500000,\n'
              '\t\t"Package": 2.389197,\n'
              '\t\t"unit": "W"\n'
              '\t}\n'
              '}\n'
              ']')
    m = _parse_intel_gpu_top(sample)
    assert m == {"busy_pct": 12.5, "freq_mhz": 2400.0, "power_watts": 3.5}  # 初始化帧跳过、busy 取 max


def test_parse_intel_gpu_top_unparseable_returns_none():
    from llm_manager.devices.adapters import _parse_intel_gpu_top
    assert _parse_intel_gpu_top("") is None
    assert _parse_intel_gpu_top("garbage\nnot json") is None


def test_device_info_new_fields_default_none():
    from llm_manager.devices import DeviceInfo
    d = DeviceInfo("X", "GPU", "VRAM", 1, 1, 0, 5.0, None)
    assert d.freq_mhz is None and d.power_watts is None  # 默认值 → 现有构造点零改动


def test_device_info_response_accepts_new_fields():
    # _to_schema 用 asdict(d) 展开:Pydantic 模型必须同步带两字段,否则 TypeError
    from llm_manager.gateway.api.devices import DeviceInfoResponse
    resp = DeviceInfoResponse(**{
        "device_name": "Intel UHD Graphics (Alder Lake-N)", "device_type": "GPU (iGPU)",
        "memory_type": "Shared RAM", "total_memory_mb": 16384, "available_memory_mb": 8192,
        "used_memory_mb": 8192, "usage_percentage": 42.0, "temperature_celsius": None,
        "freq_mhz": 2400.0, "power_watts": 3.5,
    })
    assert resp.freq_mhz == 2400.0 and resp.power_watts == 3.5


# ==================== AMD(amdgpu sysfs)====================


def _make_amdgpu_sysfs(tmp_path, busy="55", vram_total=8 * 1024**3, vram_used=3 * 1024**3, temp_mc=55000):
    """假 /sys/class/drm 树:card0 = amdgpu(带 vram/busy/hwmon)。"""
    drm = tmp_path / "sys" / "class" / "drm"
    card0 = drm / "card0" / "device"
    card0.mkdir(parents=True)
    card0.joinpath("uevent").write_text("DRIVER=amdgpu\nPCI_ID=1002:15fe\n")
    card0.joinpath("gpu_busy_percent").write_text(busy)
    card0.joinpath("mem_info_vram_total").write_text(str(vram_total))
    card0.joinpath("mem_info_vram_used").write_text(str(vram_used))
    hwmon = card0 / "hwmon" / "hwmon0"
    hwmon.mkdir(parents=True)
    hwmon.joinpath("temp1_input").write_text(str(temp_mc))
    return drm


def test_amd_adapter_basic(monkeypatch, tmp_path):
    from llm_manager.devices import adapters as ad

    monkeypatch.setattr(ad.os, "name", "posix")
    monkeypatch.setattr(ad, "_DRM_CLASS", _make_amdgpu_sysfs(tmp_path))
    out = ad.AmdLinuxAdapter().enumerate()
    assert len(out) == 1
    info = out[0]
    assert info.device_name == "AMD Radeon 780M Graphics"   # 1002:15fe 映射
    assert info.device_type == "GPU (APU)" and info.memory_type == "VRAM"
    assert info.usage_percentage == 55.0
    assert info.total_memory_mb == 8 * 1024 and info.used_memory_mb == 3 * 1024
    assert info.temperature_celsius == 55.0                 # 55000 m°C → 55°C


def test_amd_adapter_missing_fields_degraded(monkeypatch, tmp_path):
    from llm_manager.devices import adapters as ad

    monkeypatch.setattr(ad.os, "name", "posix")
    drm = tmp_path / "sys" / "class" / "drm"
    card0 = drm / "card0" / "device"
    card0.mkdir(parents=True)
    card0.joinpath("uevent").write_text("DRIVER=amdgpu\nPCI_ID=1002:1640\n")
    monkeypatch.setattr(ad, "_DRM_CLASS", drm)  # 无 busy/vram/hwmon
    out = ad.AmdLinuxAdapter().enumerate()
    assert len(out) == 1  # 识别与指标解耦
    assert out[0].usage_percentage == 0.0 and out[0].total_memory_mb == 0
    assert out[0].temperature_celsius is None
    assert out[0].device_name == "AMD Radeon (1002:1640)"  # 未知 ID 兜底


def test_amd_adapter_skips_non_amdgpu(monkeypatch, tmp_path):
    # 混合树(card0=i915 + card1=amdgpu + connector)→ 仅 amdgpu 卡上报,识别过滤真实驱动
    from llm_manager.devices import adapters as ad

    monkeypatch.setattr(ad.os, "name", "posix")
    monkeypatch.setattr(ad, "_DRM_CLASS", _make_i915_sysfs(tmp_path))
    out = ad.AmdLinuxAdapter().enumerate()
    assert len(out) == 1
    assert out[0].device_name == "AMD Radeon 780M Graphics"


def test_amd_adapter_available_clamped_non_negative(monkeypatch, tmp_path):
    # used 瞬时读穿 total → available 钳到 0,不出现负数
    from llm_manager.devices import adapters as ad

    monkeypatch.setattr(ad.os, "name", "posix")
    monkeypatch.setattr(ad, "_DRM_CLASS", _make_amdgpu_sysfs(
        tmp_path, vram_total=8 * 1024**3, vram_used=10 * 1024**3))
    out = ad.AmdLinuxAdapter().enumerate()
    assert out[0].total_memory_mb == 8 * 1024 and out[0].used_memory_mb == 10 * 1024
    assert out[0].available_memory_mb == 0


def test_amd_adapter_windows_and_missing_sysfs(monkeypatch, tmp_path):
    from llm_manager.devices import adapters as ad

    monkeypatch.setattr(ad, "_DRM_CLASS", _make_amdgpu_sysfs(tmp_path))
    monkeypatch.setattr(ad.os, "name", "nt")
    assert ad.AmdLinuxAdapter().enumerate() == []

    monkeypatch.setattr(ad.os, "name", "posix")
    monkeypatch.setattr(ad, "_DRM_CLASS", tmp_path / "nonexistent")
    assert ad.AmdLinuxAdapter().enumerate() == []

