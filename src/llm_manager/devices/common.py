"""设备适配器共享辅助:_DRM_CLASS/_drm_cards(DRM sysfs)、_system_mem(系统内存)、
_hwmon_temp1(hwmon 温度)、_read_float/_read_int_mb(数值读取)以及 Windows LHM 运行时
(单例 _lhm_computer + 跨设备共享折叠 _aggregate_sensors;设备私有解析如 CPU Tctl 温度
在各设备文件内部,遵循「每设备文件按平台分割路径」)。"""

from __future__ import annotations

import atexit
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING

import psutil

if TYPE_CHECKING:
    from . import DeviceInfo

_DRM_CLASS = Path("/sys/class/drm")  # 模块级常量,测试 monkeypatch 重定向


def _system_mem() -> tuple[int, int, int]:
    """系统 RAM 快照 (total, avail, used) MB;psutil 失败 → (0,0,0) 降级不抛。"""
    try:
        mem = psutil.virtual_memory()
        return (
            int(mem.total // (1024 * 1024)),
            int(mem.available // (1024 * 1024)),
            int(mem.used // (1024 * 1024)),
        )
    except Exception:  # noqa: BLE001
        return (0, 0, 0)


def _hwmon_temp1(dev: Path) -> float | None:
    """GPU hwmon 封装温度(temp1_input,单位 10⁻³ °C)→ 摄氏度;无 hwmon/读失败 → None。
    Intel i915 与 AMD amdgpu 共用。"""
    try:
        for hwmon in dev.glob("hwmon/hwmon*"):
            raw = hwmon.joinpath("temp1_input").read_text(encoding="ascii").strip()
            return float(raw) / 1000.0
    except (OSError, ValueError):
        pass
    return None


def _drm_cards() -> list[Path]:
    """/sys/class/drm/cardN(GPU 设备节点;跳过 cardN-* connector)。OSError → []。"""
    try:
        return sorted(
            p for p in _DRM_CLASS.iterdir() if p.name.startswith("card") and "-" not in p.name
        )
    except OSError:
        return []


def _uevent_text(dev: Path) -> str:
    """dev/uevent 全文;读失败 → ""。Intel/AMD 共用。"""
    try:
        return dev.joinpath("uevent").read_text(encoding="ascii", errors="ignore")
    except OSError:
        return ""


def _uevent_driver(dev: Path, driver: str) -> bool:
    """uevent 含 DRIVER=<driver> → True(i915/amdgpu 识别,比 vendor 更准)。"""
    return f"DRIVER={driver}" in _uevent_text(dev)


def _uevent_pci_id(dev: Path) -> str | None:
    """uevent 的 PCI_ID(如 8086:46d1)→ 小写 pci_id;无 → None。"""
    for line in _uevent_text(dev).splitlines():
        if line.startswith("PCI_ID="):
            return line.split("=", 1)[1].strip().lower()
    return None


def _read_float(path: Path) -> float:
    try:
        return float(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return 0.0


def _read_int_mb(path: Path) -> int:
    try:
        return int(path.read_text(encoding="ascii").strip()) // (1024 * 1024)
    except (OSError, ValueError):
        return 0


# ==================== LHM(Windows,LibreHardwareMonitor)运行时====================

_LHM_DLL = Path(__file__).resolve().parents[1] / "assets" / "dll" / "LibreHardwareMonitorLib.dll"


def is_lhm_available() -> bool:
    """pythonnet 可 import + LHM DLL 存在。_lhm_computer 惰性初始化前的快速判定。"""
    try:
        import clr  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return False
    return _LHM_DLL.exists()


_LHM_COMPUTER = None  # 模块级单例,lazy init
_LHM_LOCK = (
    threading.Lock()
)  # 防 monitor.refresh() 跨 asyncio.to_thread 并发 → Computer 双初始化/泄漏


def _close_lhm() -> None:
    global _LHM_COMPUTER
    if _LHM_COMPUTER is not None:
        try:
            _LHM_COMPUTER.Close()
        except Exception:  # noqa: BLE001, S110 — 进程退出收尾,Close 失败可忽略
            pass
        _LHM_COMPUTER = None


def _lhm_computer():
    """共享 LHM Computer 单例。**契约:永不抛**(返回 Computer | None)——初始化失败→None,
    使各适配器 Windows 分支(CPU/Intel/AMD)降级而非 raise。
    惰性首次调用初始化(非模块加载时):import clr + AddReference + Computer() + Open() 只首次发生(Lock double-check);
    其后 fast-path = 缓存返回。is_lhm_available() 为假→直接 None。IsCpu+IsGpu 一次开,共用。"""
    global _LHM_COMPUTER
    if not is_lhm_available():
        return None
    if _LHM_COMPUTER is None:
        with _LHM_LOCK:
            if _LHM_COMPUTER is None:  # double-checked locking
                try:
                    import clr  # type: ignore[import-not-found]

                    clr.AddReference(str(_LHM_DLL))  # type: ignore[attr-defined]
                    from LibreHardwareMonitor.Hardware import (  # type: ignore[import-not-found]
                        Computer,
                    )

                    c = Computer()
                    c.IsGpuEnabled = True
                    c.IsCpuEnabled = True
                    c.Open()
                    _LHM_COMPUTER = c
                    atexit.register(_close_lhm)
                except Exception:  # noqa: BLE001 — 初始化失败(DLL 损坏/Open 失败等)→ None,调用方降级
                    return None
    return _LHM_COMPUTER


def _aggregate_sensors(device_name: str, sensors: Iterator[tuple[str, str, float]]) -> DeviceInfo:
    """纯函数:把 LHM 传感器元组折叠成 DeviceInfo。
    跨设备共享(Intel/AMD 的 LHM Gpu 折叠同一实现);各自遍历骨架在各设备文件。
    温度取同硬件所有温度传感器最大值;频率取 Clock/Core 传感器(LHM 的 "GPU Core",
    对齐 nvidia-smi clocks.gr / intel_gpu_top frequency.actual 的核心频率语义)。"""
    from . import DeviceInfo

    core_load = 0.0
    temp_c = None
    freq_mhz = 0.0
    ded_used = ded_total = shared_used = shared_total = 0.0
    for stype, sname, val in sensors:
        if stype == "Load" and ("Core" in sname or "3D" in sname or "D3D" in sname):
            core_load = max(core_load, val)
        elif stype == "Clock" and "Core" in sname:
            freq_mhz = max(freq_mhz, val)
        elif stype == "SmallData":
            if "Dedicated" in sname and "Used" in sname:
                ded_used = val
            elif "Dedicated" in sname and "Total" in sname:
                ded_total = val
            elif "Shared" in sname and "Used" in sname:
                shared_used = val
            elif "Shared" in sname and "Total" in sname:
                shared_total = val
        elif stype == "Temperature":
            temp_c = max(temp_c, val) if temp_c is not None else val
    total = ded_total + shared_total
    used = ded_used + shared_used
    if total <= 0:
        total = used if used > 0 else 512.0
    return DeviceInfo(
        device_name,
        "GPU (APU)",
        "Shared+Ded",
        int(total),
        int(total - used),
        int(used),
        float(core_load),
        round(temp_c) if temp_c is not None else None,
        float(freq_mhz) if freq_mhz > 0 else None,
    )


def _enumerate_lhm(hw_type: str) -> list[DeviceInfo]:
    """Windows LHM 指定硬件类型的枚举(GpuIntel/GpuAmd)。_lhm_computer 不可用 → []。
    Intel/AMD 适配器共用的 Windows 分支骨架(唯一差异是 HardwareType 串)。"""
    c = _lhm_computer()
    if c is None:
        return []
    out: list[DeviceInfo] = []
    for hw in c.Hardware:
        if str(hw.HardwareType) != hw_type:
            continue
        try:
            hw.Update()
            sensors = (
                (str(s.SensorType), str(s.Name), s.Value if s.Value is not None else 0.0)
                for s in hw.Sensors
            )
            out.append(_aggregate_sensors(str(hw.Name), sensors))
        except Exception:  # noqa: BLE001, S110 — 单个 LHM GPU 传感器读取失败 → 跳过该 GPU,继续其余
            pass
    return out
