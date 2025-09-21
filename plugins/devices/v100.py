import GPUtil
from typing import Tuple
from plugins.devices.Base_Class import DevicePlugin
import logging

logger = logging.getLogger(__name__)

class V100Device(DevicePlugin):
    """V100设备插件"""

    def __init__(self):
        super().__init__("v100")

    def is_online(self) -> bool:
        """检查V100是否在线"""
        try:
            gpus = GPUtil.getGPUs()
            for gpu in gpus:
                # 检查GPU名称是否包含V100
                if "V100" in gpu.name:
                    logger.debug(f"检测到V100: {gpu.name}")
                    return True
            return False
        except Exception as e:
            logger.error(f"检查V100在线状态失败: {e}")
            return False

    def get_memory_info(self) -> Tuple[int, int, int]:
        """获取V100显存信息"""
        try:
            gpus = GPUtil.getGPUs()
            for gpu in gpus:
                if "V100" in gpu.name:
                    total_mb = int(gpu.memoryTotal)
                    used_mb = int(gpu.memoryUsed)
                    available_mb = int(gpu.memoryFree)

                    logger.debug(f"V100显存: 总={total_mb}MB, 可用={available_mb}MB, 已用={used_mb}MB")
                    return total_mb, available_mb, used_mb

            logger.warning("未找到V100 GPU")
            return 0, 0, 0

        except Exception as e:
            logger.error(f"获取V100显存信息失败: {e}")
            return 0, 0, 0

