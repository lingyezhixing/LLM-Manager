"""LHM(Windows,LibreHardwareMonitor)适配器:传感器 → DeviceInfo。"""
from __future__ import annotations

import atexit
import threading
from pathlib import Path
from typing import Iterator

from . import DeviceInfo

# ==================== LHM(Windows,LibreHardwareMonitor)====================

_LHM_DLL = Path(__file__).resolve().parents[1] / "assets" / "dll" / "LibreHardwareMonitorLib.dll"


def is_lhm_available() -> bool:
    """pythonnet 可 import + LHM DLL 存在。devices.build_adapters() 装配位一次性判断
    (DLL 路径复用 _LHM_DLL 单一来源)。"""
    try:
        import clr  # type: ignore[import-not-found]  # noqa: F401
    except ImportError:
        return False
    return _LHM_DLL.exists()


_LHM_COMPUTER = None  # 模块级单例,lazy init
_LHM_LOCK = threading.Lock()  # 防 monitor.refresh() 跨 asyncio.to_thread 并发 → Computer 双初始化/泄漏


def _close_lhm() -> None:
    global _LHM_COMPUTER
    if _LHM_COMPUTER is not None:
        try:
            _LHM_COMPUTER.Close()
        except Exception:  # noqa: BLE001 — 进程退出收尾,Close 失败可忽略
            pass
        _LHM_COMPUTER = None


def _lhm_computer():
    """共享 LHM Computer 单例。**契约:永不抛**(返回 Computer | None)——初始化失败→None,
    使 CpuAdapter.enumerate/LhmAdapter.enumerate 降级而非 raise(防 _lhm_cpu_temp 在
    CpuAdapter.enumerate 的 try 外调用时穿透)。
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
                    from LibreHardwareMonitor.Hardware import Computer  # type: ignore[import-not-found]
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
    """Pure: fold LHM sensor tuples into DeviceInfo. Port semantics from legacy amd_780m.py."""
    core_load = 0.0
    temp_c = None
    ded_used = ded_total = shared_used = shared_total = 0.0
    for stype, sname, val in sensors:
        if stype == "Load" and ("Core" in sname or "3D" in sname or "D3D" in sname):
            core_load = max(core_load, val)
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
            temp_c = val
    total = ded_total + shared_total
    used = ded_used + shared_used
    if total <= 0:
        total = used if used > 0 else 512.0
    return DeviceInfo(
        device_name, "GPU (APU)", "Shared+Ded",
        int(total), int(total - used), int(used), float(core_load),
        int(round(temp_c)) if temp_c is not None else None,
    )


class LhmAdapter:
    """LHM 的 GpuAmd/GpuIntel 硬件 → 经 _aggregate_sensors → DeviceInfo。_lhm_computer 不可用 → []。
    NVIDIA 不走此(由 NvidiaAdapter 负责),避免重复计数。"""

    def enumerate(self) -> list[DeviceInfo]:
        c = _lhm_computer()
        if c is None:
            return []
        out: list[DeviceInfo] = []
        for hw in c.Hardware:
            if str(hw.HardwareType) not in ("GpuAmd", "GpuIntel"):
                continue
            try:
                hw.Update()
                sensors = (
                    (str(s.SensorType), str(s.Name), s.Value if s.Value is not None else 0.0)
                    for s in hw.Sensors
                )
                out.append(_aggregate_sensors(str(hw.Name), sensors))
            except Exception:  # noqa: BLE001 — 单个 LHM GPU 传感器读取失败 → 跳过该 GPU,继续其余
                pass
        return out


def _lhm_cpu_temp() -> float | None:
    """从共享 LHM Computer 读 Cpu 硬件的 Tctl/Tdie 温度。不可用(无 LHM / 无 Cpu / 异常)→ None。"""
    c = _lhm_computer()
    if c is None:
        return None
    try:
        for hw in c.Hardware:
            if str(hw.HardwareType) != "Cpu":
                continue
            hw.Update()
            for s in hw.Sensors:
                if str(s.SensorType) == "Temperature" and (
                    "Tctl" in str(s.Name) or "Tdie" in str(s.Name)
                ):
                    return float(s.Value) if s.Value is not None else None
    except Exception:  # noqa: BLE001 — 读 LHM CPU 温度失败 → None
        return None
    return None
