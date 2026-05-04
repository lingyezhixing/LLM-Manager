from __future__ import annotations


from llm_manager.schemas.device import DeviceState, DeviceStatus
from llm_manager.plugins.base_device import DevicePlugin


class CPUDevice(DevicePlugin):
    name = "cpu"

    def is_available(self) -> bool:
        return True

    def get_status(self) -> DeviceStatus:
        try:
            import psutil

            mem = psutil.virtual_memory()
            return DeviceStatus(
                name=self.name,
                state=DeviceState.ONLINE,
                memory_total_mb=int(mem.total / (1024 * 1024)),
                memory_used_mb=int(mem.used / (1024 * 1024)),
                memory_free_mb=int(mem.available / (1024 * 1024)),
                utilization=mem.percent / 100.0,
            )
        except ImportError:
            return DeviceStatus(name=self.name, state=DeviceState.OFFLINE)
