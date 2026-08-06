"""5 个 DeviceAdapter 实现:nvidia-smi / Intel i915 sysfs / amdgpu sysfs / LHM / psutil。

统一降级语义:识别失败(数据源不可用)→ [] 静默;识别成功但指标缺失 → None/0,
设备照常出现。enumerate() 永不抛(内部全兜底)。"""
from __future__ import annotations

import atexit
import os
import psutil
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Iterator, NamedTuple

from . import DeviceInfo

_DRM_CLASS = Path("/sys/class/drm")  # 模块级常量,测试 monkeypatch 重定向


class _GpuRow(NamedTuple):
    name: str
    total_mb: int
    used_mb: int
    free_mb: int
    util_pct: float
    temp_c: float | None


def _parse_smi(stdout: str) -> list[_GpuRow]:
    rows: list[_GpuRow] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 6:
            continue
        try:
            name = parts[0]
            total = int(parts[1])
            used = int(parts[2])
            free = int(parts[3])
            util = float(parts[4])
            temp = float(parts[5]) if parts[5] else None
            rows.append(_GpuRow(name, total, used, free, util, temp))
        except (ValueError, IndexError):
            continue
    return rows


def _run_smi() -> str:
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return ""
    try:
        r = subprocess.run(
            [smi, "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001 — nvidia-smi 子进程异常/超时 → 视作无 NVIDIA,返回空
        return ""


class NvidiaAdapter:
    """nvidia-smi → DeviceInfo(device_name=产品原始名)。无 nvidia-smi / 无 NVIDIA → []。
    字段映射(复用 _GpuRow):total_memory_mb=memory.total;used_memory_mb=memory.used;
    available_memory_mb=memory.free;usage_percentage=utilization.gpu;
    temperature_celsius=temperature.gpu。"""

    def enumerate(self) -> list[DeviceInfo]:
        return [
            DeviceInfo(row.name, "GPU", "VRAM", row.total_mb, row.free_mb, row.used_mb, row.util_pct, row.temp_c)
            for row in _parse_smi(_run_smi())
        ]


def _system_mem() -> tuple[int, int, int]:
    """系统 RAM 快照 (total, avail, used) MB;psutil 失败 → (0,0,0) 降级不抛。"""
    try:
        mem = psutil.virtual_memory()
        return (int(mem.total // (1024 * 1024)), int(mem.available // (1024 * 1024)),
                int(mem.used // (1024 * 1024)))
    except Exception:  # noqa: BLE001
        return (0, 0, 0)


class CpuAdapter:
    """主机 CPU:psutil 取 RAM/占用 + LHM Cpu Tctl 取温度。device_name='CPU'(token 含 cpu)。
    CPU 物理恒在 → 恒返回 1 元素:psutil 调用包 try/except,失败返回降级 DeviceInfo(零值/温度 None)不抛。"""

    def enumerate(self) -> list[DeviceInfo]:
        total, avail, used = _system_mem()
        try:
            usage = float(psutil.cpu_percent(interval=None))
        except Exception:  # noqa: BLE001
            return [DeviceInfo("CPU", "CPU", "RAM", total, avail, used, 0.0, None)]
        return [DeviceInfo("CPU", "CPU", "RAM", total, avail, used, usage, _lhm_cpu_temp())]


# ==================== Intel iGPU(i915 sysfs)====================
_INTEL_IGPU_NAMES = {
    "8086:46d0": "Intel UHD Graphics (Alder Lake-N)",
    "8086:46d1": "Intel UHD Graphics (Alder Lake-N)",
}


def _intel_gpu_name(dev: Path) -> str:
    """uevent 的 PCI_ID(如 8086:46d1)→ 已知映射名,未知 → 'Intel UHD Graphics (8086:xxxx)'。"""
    try:
        for line in dev.joinpath("uevent").read_text(encoding="ascii", errors="ignore").splitlines():
            if line.startswith("PCI_ID="):
                pci_id = line.split("=", 1)[1].strip().lower()
                return _INTEL_IGPU_NAMES.get(pci_id, f"Intel UHD Graphics ({pci_id})")
    except OSError:
        pass
    return "Intel UHD Graphics"


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
        return sorted(p for p in _DRM_CLASS.iterdir() if p.name.startswith("card") and "-" not in p.name)
    except OSError:
        return []


class IntelLinuxAdapter:
    """主机/容器 Intel iGPU:扫 /sys/class/drm/cardN(0x8086 且 gpu_busy_percent 存在)→ DeviceInfo
    (device_type='GPU (iGPU)', 内存=系统 RAM 快照)。非 Linux / 无 Intel / 无 busy 接口 → []。永不抛。"""

    def enumerate(self) -> list[DeviceInfo]:
        if os.name == "nt" or not _DRM_CLASS.is_dir():
            return []
        total, avail, used = _system_mem()
        out: list[DeviceInfo] = []
        for card in _drm_cards():
            dev = card / "device"
            try:
                vendor = dev.joinpath("vendor").read_text(encoding="ascii").strip().lower()
            except OSError:
                continue
            if vendor != "0x8086" or not dev.joinpath("gpu_busy_percent").is_file():
                continue
            try:
                busy = float(dev.joinpath("gpu_busy_percent").read_text(encoding="ascii").strip())
            except (OSError, ValueError):
                busy = 0.0
            out.append(DeviceInfo(
                _intel_gpu_name(dev), "GPU (iGPU)", "Shared RAM",
                total, avail, used, busy, _hwmon_temp1(dev)))
        return out


# ==================== AMD(amdgpu sysfs,待 780M 实测校准)====================


class AmdLinuxAdapter:
    """Linux amdgpu GPU:gpu_busy_percent 利用率 + mem_info_vram_* 显存 + hwmon 温度。
    字段读不到自动降级 None/0。"""

    def enumerate(self) -> list[DeviceInfo]:
        if os.name == "nt" or not _DRM_CLASS.is_dir():
            return []
        out: list[DeviceInfo] = []
        for card in _drm_cards():
            dev = card / "device"
            if not _is_amdgpu(dev):
                continue
            total, used = _amd_vram(dev)
            busy = _read_float(dev / "gpu_busy_percent")
            out.append(DeviceInfo(
                _amd_gpu_name(dev), "GPU (APU)", "VRAM",
                total, max(total - used, 0), used, busy, _hwmon_temp1(dev)))
        return out


def _is_amdgpu(dev: Path) -> bool:
    try:
        uevent = dev.joinpath("uevent").read_text(encoding="ascii", errors="ignore")
    except OSError:
        return False
    return "DRIVER=amdgpu" in uevent


def _amd_vram(dev: Path) -> tuple[int, int]:
    """mem_info_vram_total / mem_info_vram_used(字节 → MB);缺失 → (0,0)。"""
    total = _read_int_mb(dev / "mem_info_vram_total")
    used = _read_int_mb(dev / "mem_info_vram_used")
    return total, used


def _read_int_mb(path: Path) -> int:
    try:
        return int(path.read_text(encoding="ascii").strip()) // (1024 * 1024)
    except (OSError, ValueError):
        return 0


def _read_float(path: Path) -> float:
    try:
        return float(path.read_text(encoding="ascii").strip())
    except (OSError, ValueError):
        return 0.0


_AMD_GPU_NAMES = {
    "1002:15fe": "AMD Radeon 780M Graphics",
}


def _amd_gpu_name(dev: Path) -> str:
    try:
        for line in dev.joinpath("uevent").read_text(encoding="ascii", errors="ignore").splitlines():
            if line.startswith("PCI_ID="):
                pci_id = line.split("=", 1)[1].strip().lower()
                return _AMD_GPU_NAMES.get(pci_id, f"AMD Radeon ({pci_id})")
    except OSError:
        pass
    return "AMD Radeon"


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
    """LHM 的 GpuAmd 硬件 → 经 _aggregate_sensors → DeviceInfo。_lhm_computer 不可用 → []。
    NVIDIA 不走此(由 NvidiaAdapter 负责),避免重复计数。"""

    def enumerate(self) -> list[DeviceInfo]:
        c = _lhm_computer()
        if c is None:
            return []
        out: list[DeviceInfo] = []
        for hw in c.Hardware:
            if str(hw.HardwareType) != "GpuAmd":
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
