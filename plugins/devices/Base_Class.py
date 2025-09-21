from abc import ABC, abstractmethod
from typing import Tuple, Dict, Any
import logging

logger = logging.getLogger(__name__)

class DevicePlugin(ABC):
    """设备插件基类"""

    def __init__(self, device_name: str):
        self.device_name = device_name
        logger.debug(f"设备插件初始化: {device_name}")

    @abstractmethod
    def is_online(self) -> bool:
        """
        检查设备是否在线
        返回: bool - 设备是否在线
        """
        pass

    @abstractmethod
    def get_memory_info(self) -> Tuple[int, int, int]:
        """
        获取设备内存信息
        返回: (总内存MB, 可用内存MB, 已用内存MB)
        """
        pass