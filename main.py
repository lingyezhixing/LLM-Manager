#!/usr/bin/env python3
"""
LLM-Manager 主程序入口
重构版本 - 使用Application类封装所有功能
"""

import threading
import time
import logging
import sys
import os
from typing import Optional
from utils.logger import setup_logging, get_logger
from core.config_manager import ConfigManager
from core.openai_api_router import run_api_server
from core.process_manager import get_process_manager, cleanup_process_manager
from core.monitor import Monitor

CONFIG_PATH = 'config.json'

class Application:
    """LLM-Manager 应用程序主类"""

    def __init__(self, config_path: str = CONFIG_PATH):
        """初始化应用程序"""
        self.config_path = config_path
        self.config_manager: Optional[ConfigManager] = None
        self.tray_service = None
        self.threads = []
        self.logger = None
        self.running = False
        self.monitor: Optional[Monitor] = None
        self.monitor_thread = None
        self.stop_monitor = False

    def setup_logging(self) -> None:
        """设置日志系统"""
        log_level = os.environ.get('LOG_LEVEL', 'INFO')
        setup_logging(log_level=log_level)
        self.logger = get_logger(__name__)

    def setup_signal_handlers(self) -> None:
        """设置信号处理器"""
        try:
            import signal
            def signal_handler(signum, frame):
                self.logger.info(f"接收到信号 {signum}，正在关闭应用...")
                self.shutdown()

            signal.signal(signal.SIGINT, signal_handler)
            signal.signal(signal.SIGTERM, signal_handler)
        except ImportError:
            # Windows系统可能不支持signal模块
            pass

    def initialize_config_manager(self) -> None:
        """初始化配置管理器"""
        if not os.path.exists(self.config_path):
            self.logger.error(f"配置文件不存在: {self.config_path}")
            raise FileNotFoundError(f"配置文件不存在: {self.config_path}")

        self.config_manager = ConfigManager(self.config_path)
        self.logger.info("配置管理器初始化完成")

    def initialize_monitor(self) -> None:
        """初始化监控器"""
        try:
            self.logger.info("正在初始化监控器...")

            # 创建监控器实例
            self.monitor = Monitor()

            # 读取模型主别名列表
            model_names = self.config_manager.get_model_names()
            self.logger.info(f"读取到 {len(model_names)} 个模型别名: {', '.join(model_names)}")

            # 数据库已在Monitor初始化时自动完成
            self.logger.info("数据库初始化完成")

            # 记录程序启动时间戳
            start_time = time.time()
            self.monitor.add_program_runtime_start(start_time)
            self.logger.info(f"已记录程序启动时间戳: {start_time}")

            # 启动监控线程
            self.start_monitor_thread()

        except Exception as e:
            self.logger.error(f"初始化监控器失败: {e}")
            raise

    def start_monitor_thread(self) -> None:
        """启动监控线程"""
        if not self.monitor:
            raise RuntimeError("监控器未初始化")

        def monitor_loop():
            self.logger.info("监控线程启动")
            while not self.stop_monitor:
                try:
                    current_time = time.time()
                    self.monitor.update_program_runtime_end(current_time)
                    time.sleep(10)  # 每10秒更新一次
                except Exception as e:
                    self.logger.error(f"监控线程更新失败: {e}")
                    time.sleep(10)
            self.logger.info("监控线程停止")

        self.monitor_thread = threading.Thread(target=monitor_loop, daemon=True)
        self.monitor_thread.start()
        self.threads.append(self.monitor_thread)
        self.logger.info("监控线程已启动")

    def start_api_server(self) -> None:
        """启动API服务器"""
        if not self.config_manager:
            raise RuntimeError("配置管理器未初始化")

        self.logger.info("正在启动API服务器...")

        api_thread = threading.Thread(
            target=run_api_server,
            args=(self.config_manager,),
            daemon=False  # 不能设置为daemon，否则程序会立即退出
        )
        api_thread.start()
        self.threads.append(api_thread)
        self.logger.info("API服务器启动完成")

    def start_tray_service(self) -> None:
        """启动系统托盘服务"""
        try:
            from core.tray import SystemTray

            if not self.config_manager:
                raise RuntimeError("配置管理器未初始化")

            self.logger.info("正在启动系统托盘服务...")
            self.tray_service = SystemTray(self.config_manager)

            # 设置退出回调
            self.tray_service.set_exit_callback(self._on_tray_exit)

            def tray_thread_func():
                try:
                    self.tray_service.start_tray()
                except Exception as e:
                    self.logger.error(f"托盘服务运行失败: {e}")
                    self.logger.info("托盘服务失败，应用程序退出")
                    self.shutdown()

            # 托盘线程不能设置为daemon=True，否则程序会立即退出
            tray_thread = threading.Thread(target=tray_thread_func, daemon=False)
            tray_thread.start()
            self.threads.append(tray_thread)
            self.logger.info("系统托盘服务已启动")

        except Exception as e:
            self.logger.error(f"启动托盘服务失败: {e}")

    def _on_tray_exit(self) -> None:
        """托盘退出回调"""
        self.logger.info("托盘服务请求退出")
        self.shutdown()

    def initialize(self) -> None:
        """初始化应用程序"""
        self.setup_logging()

        self.logger.info("LLM-Manager 启动中...")
        self.logger.info(f"Python 版本: {sys.version}")
        self.logger.info(f"工作目录: {os.getcwd()}")

        # 设置信号处理器
        self.setup_signal_handlers()

        # 初始化配置管理器
        self.initialize_config_manager()

        # 初始化监控器
        self.initialize_monitor()

        # 初始化进程管理器
        process_manager = get_process_manager()
        self.logger.info("进程管理器初始化完成")

    def start(self) -> None:
        """启动应用程序"""
        try:
            self.initialize()

            # 启动核心服务
            self.start_api_server()

            # 启动系统托盘服务
            self.start_tray_service()

            self.running = True
            self.logger.info("LLM-Manager 运行中，按 Ctrl+C 退出...")

            # 主循环
            self.run_main_loop()

        except Exception as e:
            self.handle_startup_error(e)

    def run_main_loop(self) -> None:
        """运行主循环"""
        try:
            while self.running:
                time.sleep(1)
        except KeyboardInterrupt:
            self.logger.info("接收到键盘中断信号")
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        """关闭应用程序"""
        if not self.running:
            return

        self.logger.info("正在关闭应用程序...")
        self.running = False

        try:
            # 停止监控线程
            if self.monitor_thread and self.monitor_thread.is_alive():
                self.stop_monitor = True
                self.monitor_thread.join(timeout=5)
                self.logger.info("监控线程已停止")

            # 更新程序结束时间戳
            if self.monitor:
                try:
                    end_time = time.time()
                    self.monitor.update_program_runtime_end(end_time)
                    self.logger.info(f"已更新程序结束时间戳: {end_time}")
                    self.monitor.close()
                    self.logger.info("监控器已关闭")
                except Exception as e:
                    self.logger.error(f"关闭监控器失败: {e}")

            # 清理进程管理器
            try:
                cleanup_process_manager()
                self.logger.info("进程管理器已清理")
            except Exception as e:
                self.logger.error(f"清理进程管理器失败: {e}")

        except Exception as e:
            self.logger.error(f"关闭应用程序时发生错误: {e}")
        finally:
            self.logger.info("应用程序已退出")

    def handle_startup_error(self, error: Exception) -> None:
        """处理启动错误"""
        error_msg = f"致命错误: {error}"
        print(error_msg)
        if self.logger:
            self.logger.error(f"应用程序启动失败: {error}", exc_info=True)
        sys.exit(1)

def main():
    """主函数入口"""
    app = Application()
    app.start()

if __name__ == "__main__":
    main()