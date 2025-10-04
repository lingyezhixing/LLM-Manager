import logging
import os
import glob
from datetime import datetime
from typing import Optional

class LogManager:
    def __init__(self, log_level: str = "INFO", log_dir: str = "logs"):
        self.log_level = log_level
        self.log_dir = log_dir
        self.loggers = {}

        # 创建日志目录
        if not os.path.exists(log_dir):
            os.makedirs(log_dir)

        # 清理旧日志文件，保留最新的10个
        self._cleanup_old_logs()

        # 生成新的日志文件名
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        self.current_log_file = os.path.join(log_dir, f"LLM-Manager_{timestamp}.log")

        # 设置日志器（同时输出到控制台和文件）
        self.setup_root_logger()

    def _cleanup_old_logs(self):
        """清理旧日志文件，保留最新的10个"""
        log_pattern = os.path.join(self.log_dir, "LLM-Manager_*.log")
        log_files = glob.glob(log_pattern)

        # 按修改时间排序，保留最新的10个
        log_files.sort(key=lambda x: os.path.getmtime(x), reverse=True)

        for log_file in log_files[10:]:
            try:
                os.remove(log_file)
                print(f"已删除旧日志文件: {log_file}")
            except Exception as e:
                print(f"删除日志文件失败 {log_file}: {e}")

    def setup_root_logger(self):
        """设置根日志器（同时输出到控制台和文件）"""
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

        # 控制台处理器
        console_handler = logging.StreamHandler()
        console_handler.setLevel(numeric_level)
        console_handler.setFormatter(formatter)
        root_logger.addHandler(console_handler)

        # 文件处理器
        file_handler = logging.FileHandler(self.current_log_file, encoding='utf-8')
        file_handler.setLevel(numeric_level)
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)

        # 记录日志文件位置
        print(f"日志文件: {self.current_log_file}")

    def get_log_file(self) -> str:
        """获取当前日志文件路径"""
        return self.current_log_file

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
        if not isinstance(numeric_level, int):
            raise ValueError(f'Invalid log level: {level}')

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