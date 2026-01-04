import os
import logging
from typing import Dict, Any
import clr

from plugins.devices.Base_Class import DevicePlugin

logger = logging.getLogger(__name__)

class AMD780MDevice(DevicePlugin):
    """AMD Radeon 780M 设备监控插件"""

    def __init__(self):
        super().__init__("780M")
        self.computer = None
        self.gpu_hardware = None
        self.cpu_hardware = None
        self._init_hardware_monitor()

    def _init_hardware_monitor(self):
        try:
            current_dir = os.path.dirname(os.path.abspath(__file__))
            project_root = os.path.abspath(os.path.join(current_dir, "../.."))
            dll_path = os.path.join(project_root, "utils", "dll", "LibreHardwareMonitorLib.dll")

            if not os.path.exists(dll_path):
                dll_path = os.path.abspath(os.path.join(current_dir, "../../utils/dll/LibreHardwareMonitorLib.dll"))
                if not os.path.exists(dll_path):
                    return

            clr.AddReference(dll_path)
            from LibreHardwareMonitor.Hardware import Computer

            self.computer = Computer()
            self.computer.IsGpuEnabled = True 
            self.computer.IsCpuEnabled = True 
            self.computer.Open()
            
        except Exception as e:
            logger.error(f"LHM Init Failed: {e}")

    def _find_hardware(self):
        if not self.computer: return
        for hardware in self.computer.Hardware:
            if str(hardware.HardwareType) == "GpuAmd":
                self.gpu_hardware = hardware
            if str(hardware.HardwareType) == "Cpu" and "Ryzen" in str(hardware.Name):
                self.cpu_hardware = hardware

    def is_online(self) -> bool:
        if not self.gpu_hardware: self._find_hardware()
        return self.gpu_hardware is not None

    def get_devices_info(self) -> Dict[str, Any]:
        empty_info = {
            'device_type': 'GPU (APU)', 'memory_type': 'DDR/LPDDR',
            'total_memory_mb': 0, 'available_memory_mb': 0,
            'used_memory_mb': 0, 'usage_percentage': 0.0,
            'temperature_celsius': None
        }

        try:
            if not self.gpu_hardware: self._find_hardware()
            if not self.gpu_hardware: return empty_info

            self.gpu_hardware.Update()
            if self.cpu_hardware: self.cpu_hardware.Update()

            core_load = 0.0
            temp_c = None
            ded_used = 0.0; ded_total = 0.0
            shared_used = 0.0; shared_total = 0.0
            
            # 1. 读 GPU (非 Admin 可读)
            for sensor in self.gpu_hardware.Sensors:
                s_type = str(sensor.SensorType)
                s_name = str(sensor.Name)
                s_val = sensor.Value if sensor.Value is not None else 0.0

                if s_type == "Load" and ("Core" in s_name or "3D" in s_name):
                    core_load = max(core_load, s_val)
                
                if s_type == "SmallData":
                    if "Dedicated" in s_name:
                        if "Used" in s_name: ded_used = s_val
                        elif "Total" in s_name: ded_total = s_val
                    elif "Shared" in s_name:
                        if "Used" in s_name: shared_used = s_val
                        elif "Total" in s_name: shared_total = s_val
                    elif "GPU Memory Total" in s_name and ded_total == 0:
                        ded_total = s_val

                if s_type == "Temperature":
                    temp_c = s_val

            # 2. 读 CPU (Admin 可读) -> 如果读到了，通常比 GPU 传感器更准(更热)
            if self.cpu_hardware:
                for sensor in self.cpu_hardware.Sensors:
                    if str(sensor.SensorType) == "Temperature" and "Tctl/Tdie" in str(sensor.Name):
                        cpu_temp = sensor.Value
                        if cpu_temp is not None:
                            # 只有当 GPU 没温度，或者 CPU 温度更高时替换
                            if temp_c is None or cpu_temp > temp_c:
                                temp_c = cpu_temp
                        break

            # 3. 汇总
            total_mem = ded_total + shared_total
            if total_mem <= 0: total_mem = used_mem if (used_mem := ded_used + shared_used) > 0 else 512
            available_mem = total_mem - (ded_used + shared_used)

            if temp_c is not None:
                temp_c = int(round(temp_c))

            return {
                'device_type': 'GPU (APU)',
                'memory_type': 'Shared+Ded',
                'total_memory_mb': int(total_mem),
                'available_memory_mb': int(available_mem),
                'used_memory_mb': int(ded_used + shared_used),
                'usage_percentage': float(core_load),
                'temperature_celsius': temp_c
            }

        except Exception as e:
            logger.error(f"780M Monitor Error: {e}")
            return empty_info

    def __del__(self):
        try:
            if self.computer: self.computer.Close()
        except: pass