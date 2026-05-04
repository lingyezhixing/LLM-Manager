from __future__ import annotations

import logging

from llm_manager.schemas.device import DeviceState, DeviceStatus
from llm_manager.plugins.base_device import DevicePlugin

logger = logging.getLogger(__name__)


class AMDDevice(DevicePlugin):
    name: str

    def __init__(self, device_name: str):
        self.name = device_name.lower()

    def is_available(self) -> bool:
        try:
            import clr  # type: ignore  # pythonnet

            return True
        except ImportError:
            return False

    def get_status(self) -> DeviceStatus:
        try:
            import clr  # type: ignore

            clr.AddReference("LibreHardwareMonitorLib")
            from LibreHardwareMonitor.Hardware import Computer

            computer = Computer()
            computer.IsGpuEnabled = True
            computer.Open()

            for hardware in computer.Hardware:
                if "amd" in hardware.Name.lower() or self.name in hardware.Name.lower():
                    hardware.Update()
                    mem_total = mem_used = temp = util = None
                    for sensor in hardware.Sensors:
                        if "Memory Total" in sensor.Name:
                            mem_total = int(sensor.Value or 0)
                        elif "Memory Used" in sensor.Name:
                            mem_used = int(sensor.Value or 0)
                        elif "Temperature" in sensor.Name and "Core" in sensor.Name:
                            temp = sensor.Value
                        elif "Load" in sensor.Name and "Core" in sensor.Name:
                            util = sensor.Value

                    computer.Close()
                    return DeviceStatus(
                        name=self.name,
                        state=DeviceState.ONLINE,
                        memory_total_mb=mem_total or 0,
                        memory_used_mb=mem_used or 0,
                        memory_free_mb=(mem_total or 0) - (mem_used or 0),
                        temperature=temp,
                        utilization=util,
                    )

            computer.Close()
            return DeviceStatus(name=self.name, state=DeviceState.OFFLINE)
        except Exception:
            logger.exception("Failed to get AMD device status")
            return DeviceStatus(name=self.name, state=DeviceState.OFFLINE)
