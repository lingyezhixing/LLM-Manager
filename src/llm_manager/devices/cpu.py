"""主机 CPU 适配器:psutil RAM/占用 + LHM Cpu Tctl 温度。"""
from __future__ import annotations

import psutil

from . import DeviceInfo
from .common import _system_mem, _lhm_cpu_temp


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
