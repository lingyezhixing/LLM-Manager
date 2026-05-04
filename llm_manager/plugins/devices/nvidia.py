from __future__ import annotations

import logging

from llm_manager.schemas.device import DeviceState, DeviceStatus
from llm_manager.plugins.base_device import DevicePlugin

logger = logging.getLogger(__name__)


class NvidiaDevice(DevicePlugin):
    name: str

    def __init__(self, device_name: str, gpu_index: int = 0):
        self.name = device_name.lower()
        self._gpu_index = gpu_index

    def is_available(self) -> bool:
        try:
            import GPUtil

            gpus = GPUtil.getGPUs()
            return self._gpu_index < len(gpus)
        except Exception:
            return False

    def get_status(self) -> DeviceStatus:
        try:
            import GPUtil

            gpus = GPUtil.getGPUs()
            if self._gpu_index >= len(gpus):
                return DeviceStatus(name=self.name, state=DeviceState.OFFLINE)

            gpu = gpus[self._gpu_index]
            return DeviceStatus(
                name=self.name,
                state=DeviceState.ONLINE,
                memory_total_mb=int(gpu.memoryTotal),
                memory_used_mb=int(gpu.memoryUsed),
                memory_free_mb=int(gpu.memoryFree),
                temperature=gpu.temperature,
                utilization=gpu.load,
            )
        except Exception:
            logger.exception("Failed to get NVIDIA GPU status")
            return DeviceStatus(name=self.name, state=DeviceState.OFFLINE)
