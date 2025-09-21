import logging
import os
from typing import Optional

class LogManager:
    def __init__(self, log_level: str = "INFO", log_dir: str = "logs"):
        self.log_level = log_level
        self.log_dir = log_dir
        self.loggers = {}

        # 创建日志目录
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        # 设置根日志器（只输出到控制台，由批处理脚本保存日志）
        self.setup_root_logger()

    def setup_root_logger(self):
        """设置根日志器（只输出到控制台，由批处理脚本保存日志）"""
        numeric_level = getattr(logging, self.log_level.upper(), None)
        if not isinstance(numeric_level, int):
            raise ValueError(f'Invalid log level: {self.log_level}')

        # 创建格式化器
        formatter = logging.Formatter(
            '%(asctime)s - [%(levelname)s] - %(name)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # 设置根日志器
        root_logger = logging.getLogger()
        root_logger.setLevel(numeric_level)

        # 清除现有处理器
        for handler in root_logger.handlers[:]:
            root_logger.removeHandler(handler)

        # 仅使用控制台处理器，日志由批处理脚本接管保存
        console_handler = logging.StreamHandler()
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

    def get_logger(self, name: str) -> logging.Logger:
        """获取指定名称的日志器"""
        if name not in self.loggers:
            logger = logging.getLogger(name)
            self.loggers[name] = logger
        return self.loggers[name]

    def set_level(self, level: str):
        """设置日志级别"""
        self.log_level = level
        numeric_level = getattr(logging, level.upper(), None)
        if isinstance(numeric_level, int):
            logging.getLogger().setLevel(numeric_level)
            for handler in logging.getLogger().handlers:
                handler.setLevel(numeric_level)

# 全局日志管理器实例
_log_manager = None

def setup_logging(log_level: str = "INFO", log_dir: str = "logs"):
    """初始化全局日志管理器"""
    global _log_manager
    _log_manager = LogManager(log_level, log_dir)

def get_logger(name: str) -> logging.Logger:
    """获取日志器"""
    if _log_manager is None:
        setup_logging()
    return _log_manager.get_logger(name)