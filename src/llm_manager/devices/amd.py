"""AMD GPU 适配器:Linux(amdgpu sysfs)/Windows(LHM)统一接口。"""

from __future__ import annotations

import os
from pathlib import Path

from . import DeviceInfo
from .common import (
    _DRM_CLASS,
    _drm_cards,
    _enumerate_lhm,  # Windows LHM 运行时
    _hwmon_temp1,
    _read_float,
    _read_int_mb,
    _uevent_driver,
    _uevent_pci_id,
)

# ==================== AMD(amdgpu sysfs + LHM)====================


def _is_amdgpu(dev: Path) -> bool:
    """uevent 含 DRIVER=amdgpu → AMD GPU(与 Intel 的 _is_i915 同构,不依赖 vendor 猜测)。"""
    return _uevent_driver(dev, "amdgpu")


def _amd_vram(dev: Path) -> tuple[int, int]:
    """mem_info_vram_total / mem_info_vram_used(字节 → MB);缺失 → (0,0)。"""
    total = _read_int_mb(dev / "mem_info_vram_total")
    used = _read_int_mb(dev / "mem_info_vram_used")
    return total, used


_AMD_GPU_NAMES = {
    "1002:15fe": "AMD Radeon 780M Graphics",
}


def _amd_gpu_name(dev: Path) -> str:
    """uevent 的 PCI_ID(如 1002:15fe)→ 已知映射名,未知 → 'AMD Radeon (1002:xxxx)'。"""
    pci_id = _uevent_pci_id(dev)
    if pci_id is None:
        return "AMD Radeon"
    return _AMD_GPU_NAMES.get(pci_id, f"AMD Radeon ({pci_id})")


class AmdAdapter:
    """AMD GPU 适配器:Linux(amdgpu sysfs)/Windows(LHM)统一接口。
    Linux:gpu_busy_percent 利用率 + mem_info_vram_* 显存 + hwmon 温度。字段读不到自动降级 None/0。
    Windows:LHM GpuAmd 硬件 → 经 _aggregate_sensors → DeviceInfo。"""

    def enumerate(self) -> list[DeviceInfo]:
        """内部 dispatch 平台:nt→LHM / posix→amdgpu sysfs。"""
        if os.name == "nt":
            return self._enumerate_windows()
        return self._enumerate_linux()

    def _enumerate_linux(self) -> list[DeviceInfo]:
        """Linux amdgpu GPU:gpu_busy_percent 利用率 + mem_info_vram_* 显存 + hwmon 温度。
        字段读不到自动降级 None/0(无 amdgpu 卡实测校准,识别与字段语义按 amdgpu 驱动文档)。"""
        if not _DRM_CLASS.is_dir():
            return []
        out: list[DeviceInfo] = []
        for card in _drm_cards():
            dev = card / "device"
            if not _is_amdgpu(dev):
                continue
            total, used = _amd_vram(dev)
            busy = _read_float(dev / "gpu_busy_percent")
            out.append(
                DeviceInfo(
                    _amd_gpu_name(dev),
                    "GPU (APU)",
                    "VRAM",
                    total,
                    max(total - used, 0),
                    used,
                    busy,
                    _hwmon_temp1(dev),
                )
            )
        return out

    def _enumerate_windows(self) -> list[DeviceInfo]:
        """Windows AMD GPU:LHM GpuAmd 硬件 → 经 _aggregate_sensors → DeviceInfo。"""
        return _enumerate_lhm("GpuAmd")
