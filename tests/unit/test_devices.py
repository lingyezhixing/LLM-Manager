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


# ==================== Intel iGPU(i915 sysfs)====================


def _make_fake_sysfs(tmp_path, *, vendor="0x8086", busy="42", pci_id="8086:46d1", temp_mc=None):
    """构造假 /sys/class/drm 树:card0(Intel)+ card1(非 Intel)+ card0-DP-1(connector)。"""
    drm = tmp_path / "sys" / "class" / "drm"
    card0 = drm / "card0" / "device"
    card0.mkdir(parents=True)
    card0.joinpath("vendor").write_text(vendor)
    card0.joinpath("uevent").write_text(f"PCI_ID={pci_id}\nDRIVER=i915\n")
    if busy is not None:
        card0.joinpath("gpu_busy_percent").write_text(busy)
    if temp_mc is not None:
        hwmon = card0 / "hwmon" / "hwmon0"
        hwmon.mkdir(parents=True)
        hwmon.joinpath("temp1_input").write_text(str(temp_mc))
    # 非 Intel 卡 + connector 节点:都不应被枚举
    card1 = drm / "card1" / "device"
    card1.mkdir(parents=True)
    card1.joinpath("vendor").write_text("0x10de")  # NVIDIA
    drm.joinpath("card0-DP-1").mkdir(parents=True)
    return drm


def test_enumerate_intel_igpus_basic(monkeypatch, tmp_path):
    from llm_manager.devices import adapters as ad

    drm = _make_fake_sysfs(tmp_path, busy="42", temp_mc=48000)
    monkeypatch.setattr(ad.os, "name", "posix")
    monkeypatch.setattr(ad, "_DRM_CLASS", drm)

    class _Mem:
        total = 16 * 1024**3
        available = 8 * 1024**3
        used = 8 * 1024**3

    monkeypatch.setattr(ad.psutil, "virtual_memory", lambda: _Mem())
    out = ad.IntelLinuxAdapter().enumerate()
    assert len(out) == 1  # 只命中 card0;card1(NVIDIA)与 connector 跳过
    info = out[0]
    assert info.device_name == "Intel UHD Graphics (Alder Lake-N)"  # 46d1 映射
    assert info.device_type == "GPU (iGPU)" and info.memory_type == "Shared RAM"
    assert info.usage_percentage == 42.0
    assert info.temperature_celsius == 48.0  # temp1_input 48000 m°C → 48°C
    assert info.total_memory_mb == 16 * 1024


def test_enumerate_intel_igpus_unknown_pci_id_fallback(monkeypatch, tmp_path):
    from llm_manager.devices import adapters as ad

    drm = _make_fake_sysfs(tmp_path, pci_id="8086:46f0")
    monkeypatch.setattr(ad.os, "name", "posix")
    monkeypatch.setattr(ad, "_DRM_CLASS", drm)
    out = ad.IntelLinuxAdapter().enumerate()
    assert len(out) == 1
    assert out[0].device_name == "Intel UHD Graphics (8086:46f0)"


def test_enumerate_intel_igpus_skips_non_intel_and_no_busy(monkeypatch, tmp_path):
    from llm_manager.devices import adapters as ad

    drm = _make_fake_sysfs(tmp_path, busy=None)  # 有 vendor 但无 gpu_busy_percent → 跳过
    monkeypatch.setattr(ad.os, "name", "posix")
    monkeypatch.setattr(ad, "_DRM_CLASS", drm)
    assert ad.IntelLinuxAdapter().enumerate() == []

    drm2 = _make_fake_sysfs(tmp_path / "b", vendor="0x10de")
    monkeypatch.setattr(ad, "_DRM_CLASS", drm2)
    assert ad.IntelLinuxAdapter().enumerate() == []


def test_enumerate_intel_igpus_windows_and_missing_sysfs(monkeypatch, tmp_path):
    from llm_manager.devices import adapters as ad

    drm = _make_fake_sysfs(tmp_path, busy="42")
    monkeypatch.setattr(ad, "_DRM_CLASS", drm)
    monkeypatch.setattr(ad.os, "name", "nt")
    assert ad.IntelLinuxAdapter().enumerate() == []  # Windows 不走 sysfs

    monkeypatch.setattr(ad.os, "name", "posix")
    monkeypatch.setattr(ad, "_DRM_CLASS", tmp_path / "nonexistent")
    assert ad.IntelLinuxAdapter().enumerate() == []  # 无 /sys/class/drm → []


def test_enumerate_intel_igpus_psutil_failure_degraded(monkeypatch, tmp_path):
    from llm_manager.devices import adapters as ad

    drm = _make_fake_sysfs(tmp_path, busy="7")
    monkeypatch.setattr(ad.os, "name", "posix")
    monkeypatch.setattr(ad, "_DRM_CLASS", drm)

    def boom():
        raise OSError("psutil broke")

    monkeypatch.setattr(ad.psutil, "virtual_memory", boom)
    out = ad.IntelLinuxAdapter().enumerate()
    assert len(out) == 1  # 降级零值,不抛
    assert out[0].total_memory_mb == 0
    assert out[0].usage_percentage == 7.0


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

