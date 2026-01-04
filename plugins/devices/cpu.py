import psutil
import os
import logging
from typing import Dict, Any
import clr

logger = logging.getLogger(__name__)

from plugins.devices.Base_Class import DevicePlugin

class CPUDevice(DevicePlugin):
    """CPU设备插件 (支持降级模式: Admin读核心 -> 无Admin读核显温度)"""

    def __init__(self):
        super().__init__("CPU")
        self.lhm_computer = None
        self.lhm_cpu = None
        self.lhm_gpu = None # 新增：用于降级读取
        
        self._init_lhm()

    def _init_lhm(self):
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

            self.lhm_computer = Computer()
            self.lhm_computer.IsCpuEnabled = True 
            # 🟢【关键】开启 GPU 监控，作为无 Admin 权限时的备选方案
            self.lhm_computer.IsGpuEnabled = True 
            self.lhm_computer.Open()

            for hardware in self.lhm_computer.Hardware:
                hw_type = str(hardware.HardwareType)
                if hw_type == "Cpu":
                    self.lhm_cpu = hardware
                # 寻找同体的核显 (GpuAmd)
                elif hw_type == "GpuAmd":
                    self.lhm_gpu = hardware

        except Exception as e:
            logger.error(f"CPU插件 LHM 初始化异常: {e}")

    def is_online(self) -> bool:
        return True

    def get_devices_info(self) -> Dict[str, Any]:
        try:
            # === 1. 基础数据 (psutil) ===
            memory = psutil.virtual_memory()
            total_mb = int(memory.total // (1024 * 1024))
            available_mb = int(memory.available // (1024 * 1024))
            used_mb = int(memory.used // (1024 * 1024))
            usage_percentage = psutil.cpu_percent(interval=None)

            # === 2. 温度读取 (智能降级逻辑) ===
            temperature = None
            
            if self.lhm_computer:
                try:
                    # A. 优先尝试读取 CPU (最准，但需 Admin)
                    cpu_temp_found = None
                    if self.lhm_cpu:
                        self.lhm_cpu.Update()
                        max_t = 0.0
                        target_t = None
                        for sensor in self.lhm_cpu.Sensors:
                            if str(sensor.SensorType) == "Temperature":
                                val = sensor.Value if sensor.Value is not None else 0
                                if val > max_t: max_t = val
                                if "Tctl" in str(sensor.Name) or "Package" in str(sensor.Name):
                                    target_t = val
                        
                        # 如果读到了非零数据，说明有权限
                        raw_cpu = target_t if target_t is not None else max_t
                        if raw_cpu > 0:
                            cpu_temp_found = raw_cpu

                    # B. 如果 CPU 没读到 (cpu_temp_found 为 None)，尝试读取 GPU (无需 Admin)
                    gpu_temp_found = None
                    if cpu_temp_found is None and self.lhm_gpu:
                        self.lhm_gpu.Update()
                        for sensor in self.lhm_gpu.Sensors:
                            if str(sensor.SensorType) == "Temperature":
                                # 只要读到任何温度 (通常是 GPU VR SoC 或 Core)
                                val = sensor.Value if sensor.Value is not None else 0
                                if val > 0:
                                    gpu_temp_found = val
                                    break # 读到一个就行，反正都是代替品

                    # 决策：优先 CPU，降级用 GPU
                    final_temp = cpu_temp_found if cpu_temp_found is not None else gpu_temp_found
                    
                    if final_temp is not None:
                        temperature = int(round(final_temp))

                except Exception:
                    pass

            return {
                'device_type': 'CPU',
                'memory_type': 'RAM',
                'total_memory_mb': total_mb,
                'available_memory_mb': available_mb,
                'used_memory_mb': used_mb,
                'usage_percentage': usage_percentage,
                'temperature_celsius': temperature
            }

        except Exception as e:
            logger.error(f"CPU info error: {e}")
            return {
                'device_type': 'CPU', 'memory_type': 'RAM',
                'total_memory_mb': 0, 'available_memory_mb': 0, 
                'used_memory_mb': 0, 'usage_percentage': 0.0, 
                'temperature_celsius': None
            }

    def __del__(self):
        try:
            if self.lhm_computer: self.lhm_computer.Close()
        except: pass