"""Device detection (NVIDIA via nvidia-smi subprocess; AMD APU via LHM adapter)
+ on-demand DeviceMonitor (rebuild-then-atomic-rebind cache; no in-place mutation)."""
from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from typing import Callable, Iterator, NamedTuple, Protocol, runtime_checkable


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
            [smi, "--query-gpu=name,memory.total,memory.used,memory.free,utilization.gpu,temperature.gpu"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        return r.stdout if r.returncode == 0 else ""
    except Exception:
        return ""


def detect_nvidia(device_name: str, name_token: str) -> DeviceInfo | None:
    for row in _parse_smi(_run_smi()):
        if name_token.lower() in row.name.lower():
            return DeviceInfo(device_name, "GPU", "VRAM", row.total_mb, row.free_mb, row.used_mb, row.util_pct, row.temp_c)
    return None


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


def detect_amd_apu(device_name: str, lhm_adapter: Callable[[], Iterator[tuple[str, str, float]]] | None) -> DeviceInfo | None:
    if lhm_adapter is None:
        return None
    try:
        return _aggregate_sensors(device_name, lhm_adapter())
    except Exception:
        return None


DEVICES: dict[str, Callable[[], DeviceInfo | None]] = {
    "rtx 4060": lambda: detect_nvidia("rtx 4060", "4060"),
    "v100": lambda: detect_nvidia("v100", "V100"),
    # "780m": wired in app.py when pythonnet + DLL available (monitoring extra)
}


@runtime_checkable
class DeviceSource(Protocol):
    def online_devices(self) -> set[str]: ...
    def snapshot(self) -> dict[str, DeviceInfo]: ...
    def refresh(self) -> None: ...


class DeviceMonitor:
    """On-demand poll + cache. refresh() rebuilds a fresh dict and atomically rebinds
    self._cache (CPython attribute store is atomic: readers see whole old/new, never torn)."""
    def __init__(self, devices: dict[str, Callable[[], DeviceInfo | None]], cache_ttl: float = 10.0) -> None:
        self._devices = devices
        self._cache: dict[str, DeviceInfo] = {}

    def refresh(self) -> None:
        new: dict[str, DeviceInfo] = {}
        for name, det in self._devices.items():
            try:
                info = det()
                if info is not None:
                    new[name] = info
            except Exception:
                pass
        self._cache = new  # atomic rebind — never mutate in place

    def online_devices(self) -> set[str]:
        return set(self._cache)

    def snapshot(self) -> dict[str, DeviceInfo]:
        return dict(self._cache)
