"""Device detection: backends enumerate all present hardware (NVIDIA via nvidia-smi,
AMD GPU via LHM, CPU via psutil) → DeviceMonitor fuzzy-matches config device names to
detected hardware (token-subset) and atomically rebinds a config-keyed cache
(+ unmatched devices keyed by raw name for display)."""
from __future__ import annotations

import atexit
import psutil
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, NamedTuple


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    device_name: str
    device_type: str
    memory_type: str
    total_memory_mb: int
    available_memory_mb: int
    used_memory_mb: int
    usage_percentage: float
    temperature_celsius: float | None


def _tokens(name: str) -> set[str]:
    """小写 + 按非字母数字拆 token。'RTX 4060 Ti'→{rtx,4060,ti};'V100-SXM2'→{v100,sxm2}。"""
    return set(re.findall(r"[a-z0-9]+", name.lower()))


def match_devices(
    referenced: set[str], candidates: list[DeviceInfo]
) -> tuple[dict[str, DeviceInfo], list[DeviceInfo]]:
    """每个 config 名取全子集(score==1.0)的候选;并列去歧义键=(精确等同, -多余 token 数, -索引)
    取最大。多余 token 数 = |detected − config|(越少越贴近 config)。一个候选只配一个 config 名(used 集)。
    返回 (config 键控匹配 dict, 未引用候选 list)。遍历 referenced 按 sorted() 保证赋值决定性。"""
    matched: dict[str, DeviceInfo] = {}
    used: set[int] = set()
    for name in sorted(referenced):
        ct = _tokens(name)
        if not ct:
            continue
        best_idx = -1
        best_key: tuple | None = None
        for i, cand in enumerate(candidates):
            if i in used:
                continue
            dt = _tokens(cand.device_name)
            if not ct <= dt:  # 要求全子集(score==1.0)
                continue
            key = (ct == dt, -len(dt - ct), -i)  # 精确等同优先 → 多余 token 最少 → 索引最小
            if best_key is None or key > best_key:
                best_key = key
                best_idx = i
        if best_idx >= 0:
            matched[name] = candidates[best_idx]
            used.add(best_idx)
    unmatched = [c for i, c in enumerate(candidates) if i not in used]
    return matched, unmatched


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
    except Exception:
        return ""


def enumerate_nvidia() -> list[DeviceInfo]:
    """nvidia-smi 全部 GPU 行 → DeviceInfo(device_name=产品原始名)。无 nvidia-smi / 无 NVIDIA → []。
    字段映射(复用 _GpuRow):
      total_memory_mb=memory.total; used_memory_mb=memory.used; available_memory_mb=memory.free;
      usage_percentage=utilization.gpu; temperature_celsius=temperature.gpu。"""
    return [
        DeviceInfo(row.name, "GPU", "VRAM", row.total_mb, row.free_mb, row.used_mb, row.util_pct, row.temp_c)
        for row in _parse_smi(_run_smi())
    ]


def enumerate_cpu() -> list[DeviceInfo]:
    """主机 CPU:psutil 取 RAM/占用 + LHM Cpu Tctl 取温度。device_name='CPU'(token 含 cpu)。
    CPU 物理恒在 → 恒返回 1 元素:psutil 调用包 try/except,失败返回降级 DeviceInfo(零值/温度 None)不抛。"""
    try:
        mem = psutil.virtual_memory()
        total = int(mem.total // (1024 * 1024))
        avail = int(mem.available // (1024 * 1024))
        used = int(mem.used // (1024 * 1024))
        usage = float(psutil.cpu_percent(interval=None))
    except Exception:
        return [DeviceInfo("CPU", "CPU", "RAM", 0, 0, 0, 0.0, None)]
    return [DeviceInfo("CPU", "CPU", "RAM", total, avail, used, usage, _lhm_cpu_temp())]


def enumerate_lhm_gpus() -> list[DeviceInfo]:
    """LHM 的 GpuAmd 硬件 → 经 _aggregate_sensors → DeviceInfo。_lhm_computer 不可用 → []。
    NVIDIA 不走此(由 enumerate_nvidia 负责),避免重复计数。"""
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
        except Exception:
            pass
    return out


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


_LHM_DLL = Path(__file__).resolve().parent / "assets" / "dll" / "LibreHardwareMonitorLib.dll"


def is_lhm_available() -> bool:
    """pythonnet 可 import + LHM DLL 存在。app.py 装配位一次性判断
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
        except Exception:
            pass
        _LHM_COMPUTER = None


def _lhm_computer():
    """共享 LHM Computer 单例。**契约:永不抛**(返回 Computer | None)——初始化失败→None,
    使 enumerate_cpu/enumerate_lhm_gpus 降级而非 raise(防 _lhm_cpu_temp 在 enumerate_cpu 的 try 外调用时穿透)。
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
                except Exception:
                    return None  # 初始化失败(DLL 损坏/Open 失败等)→ None,调用方降级
    return _LHM_COMPUTER


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
    except Exception:
        return None
    return None


class DeviceMonitor:
    """On-demand 枚举 + 模糊匹配。refresh() 跑全部枚举器 → candidates → match_devices(referenced)
    → 原子 rebind self._cache(config 名键控 + 未引用实测名键控)。"""
    def __init__(
        self,
        enumerators: list[Callable[[], list[DeviceInfo]]],
        referenced: set[str],
    ) -> None:
        self._enumerators = enumerators
        self._referenced = set(referenced)
        self._cache: dict[str, DeviceInfo] = {}

    def refresh(self) -> None:
        candidates: list[DeviceInfo] = []
        for enum in self._enumerators:
            try:
                result = enum()
                if result:
                    candidates.extend(result)
            except Exception:
                pass  # 单个后端失败不影响其他
        matched, unmatched = match_devices(self._referenced, candidates)
        cache: dict[str, DeviceInfo] = dict(matched)
        for c in unmatched:
            cache[c.device_name] = c  # 未引用:以实测名入快照供展示(对调度无害)
        self._cache = cache  # 原子 rebind

    def online_devices(self) -> set[str]:
        return set(self._cache)

    def snapshot(self) -> dict[str, DeviceInfo]:
        return dict(self._cache)


ENUMERATORS: list[Callable[[], list[DeviceInfo]]] = [enumerate_nvidia, enumerate_lhm_gpus, enumerate_cpu]
