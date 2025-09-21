import psutil
from typing import Tuple
from plugins.devices.Base_Class import DevicePlugin
import logging

logger = logging.getLogger(__name__)

class CPUDevice(DevicePlugin):
    """CPU设备插件"""

    def __init__(self):
        super().__init__("CPU")

    def is_online(self) -> bool:
        """CPU通常总是在线的"""
        return True

    def get_memory_info(self) -> Tuple[int, int, int]:
        """获取CPU内存信息"""
        try:
            memory = psutil.virtual_memory()
            total_mb = memory.total // (1024 * 1024)
            available_mb = memory.available // (1024 * 1024)
            used_mb = memory.used // (1024 * 1024)

            logger.debug(f"CPU内存: 总={total_mb}MB, 可用={available_mb}MB, 已用={used_mb}MB")
            return total_mb, available_mb, used_mb

        except Exception as e:
            logger.error(f"获取CPU内存信息失败: {e}")
            return 0, 0, 0