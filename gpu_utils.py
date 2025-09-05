# gpu_utils.py
import GPUtil
import logging
import re

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

NAME_SIMPLIFY_PATTERN = re.compile(
    r'NVIDIA|GeForce|Tesla|Laptop GPU|GPU|-SXM2-16GB|\s+\d+GB', 
    re.IGNORECASE
)
MULTI_SPACE_PATTERN = re.compile(r'\s+')

def simplify_gpu_name(name: str) -> str:
    """
    简化GPU名称，以便在配置文件中轻松匹配。
    """
    simplified = NAME_SIMPLIFY_PATTERN.sub('', name)
    simplified = MULTI_SPACE_PATTERN.sub(' ', simplified).strip().lower()
    return simplified

def get_gpu_info():
    """
    获取所有可用的NVIDIA GPU信息，并为每个GPU附加一个简化的名称。
    """
    try:
        gpus = GPUtil.getGPUs()
        if not gpus:
            logger.warning("未检测到NVIDIA GPU。")
            return []
        
        for gpu in gpus:
            gpu.simple_name = simplify_gpu_name(gpu.name)
            
        return gpus
    except Exception as e:
        logger.error(f"获取GPU信息时出错: {e}")
        return []

def get_available_vram() -> dict:
    """
    获取每张显卡的可用VRAM（单位MB）。
    返回: 一个字典 {gpu_id: free_memory_mb}
    """
    try:
        gpus = GPUtil.getGPUs()
        return {gpu.id: gpu.memoryFree for gpu in gpus}
    except Exception as e:
        logger.error(f"获取可用VRAM时出错: {e}")
        return {}

if __name__ == '__main__':
    gpus_info = get_gpu_info()
    if gpus_info:
        print("检测到的GPU信息:")
        for gpu in gpus_info:
            print(f"  ID: {gpu.id}, Name: {gpu.name}, 用于配置的简化名称: '{gpu.simple_name}', 可用显存: {gpu.memoryFree}MB")
    
    available_mem = get_available_vram()
    if available_mem:
        print("\n各GPU可用显存:")
        for gpu_id, mem in available_mem.items():
            print(f"  GPU {gpu_id}: {mem} MB")