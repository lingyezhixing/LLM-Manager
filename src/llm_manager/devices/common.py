"""设备适配器共享辅助:_DRM_CLASS/_drm_cards(DRM sysfs)、_system_mem(系统内存)、
_hwmon_temp1(hwmon 温度)、_read_float/_read_int_mb(数值读取)。"""
from __future__ import annotations

import psutil
from pathlib import Path

_DRM_CLASS = Path("/sys/class/drm")  # 模块级常量,测试 monkeypatch 重定向


def _system_mem() -> tuple[int, int, int]:
    """系统 RAM 快照 (total, avail, used) MB;psutil 失败 → (0,0,0) 降级不抛。"""
    try:
        mem = psutil.virtual_memory()
        return (int(mem.total // (1024 * 1024)), int(mem.available // (1024 * 1024)),
                int(mem.used // (1024 * 1024)))
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
        return sorted(p for p in _DRM_CLASS.iterdir() if p.name.startswith("card") and "-" not in p.name)
    except OSError:
        return []


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
