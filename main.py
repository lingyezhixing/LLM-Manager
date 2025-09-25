#!/usr/bin/env python3
"""
LLM-Manager 主程序入口
重构版本 - 使用Application类封装所有功能
优化版本：支持并行初始化和快速关闭
"""

import threading
import time
import logging
import sys
import os
import concurrent.futures
from typing import Optional
from utils.logger import setup_logging, get_logger
from core.config_manager import ConfigManager
from core.api_server import run_api_server
from core.process_manager import get_process_manager, cleanup_process_manager
from core.monitor import Monitor
from core.model_controller import ModelController

CONFIG_PATH = 'config.json'


class Application:
    """优化的LLM-Manager应用程序主类"""

    def __init__(self, config_path: str = CONFIG_PATH):
        """
        初始化应用程序

        Args:
            config_path: 配置文件路径
        """
        self.config_path = config_path
        self.config_manager: Optional[ConfigManager] = None
        self.tray_service = None
        self.threads = []
        self.logger = None
        self.running = False
        self.monitor: Optional[Monitor] = None
        self.monitor_thread = None
        self.stop_monitor = False
        self.model_controller: Optional[ModelController] = None
        self.startup_complete = threading.Event()
        self.shutdown_event = threading.Event()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

    def setup_logging(self) -> None:
        """设置日志系统"""
        if self.config_manager:
            log_level = self.config_manager.get_log_level()
        else:
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
        """优化的初始化配置管理器"""
        if not os.path.exists(self.config_path):
            self.logger.error(f"配置文件不存在: {self.config_path}")
            raise FileNotFoundError(f"配置文件不存在: {self.config_path}")

        self.config_manager = ConfigManager(self.config_path)

        # 配置管理器初始化完成后，重新设置日志级别以应用配置文件中的设置
        log_level = self.config_manager.get_log_level()
        from utils.logger import _log_manager
        if _log_manager:
            _log_manager.set_level(log_level)

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
        """优化的初始化应用程序 - 并行初始化组件"""
        self.setup_logging()

        self.logger.info("LLM-Manager 启动中...")
        self.logger.info(f"Python 版本: {sys.version}")
        self.logger.info(f"工作目录: {os.getcwd()}")

        # 设置信号处理器
        self.setup_signal_handlers()

        # 并行初始化核心组件
        def init_config():
            self.initialize_config_manager()
            return "config_manager"

        def init_process_manager():
            process_manager = get_process_manager()
            self.logger.info("进程管理器初始化完成")
            return "process_manager"

        def init_monitor():
            self.initialize_monitor()
            return "monitor"

        # 提交初始化任务
        futures = []
        futures.append(self.executor.submit(init_config))
        futures.append(self.executor.submit(init_process_manager))
        futures.append(self.executor.submit(init_monitor))

        # 等待所有初始化完成
        for future in concurrent.futures.as_completed(futures, timeout=30):
            try:
                component = future.result()
                self.logger.debug(f"组件 {component} 初始化完成")
            except Exception as e:
                self.logger.error(f"组件初始化失败: {e}")
                raise

        # 初始化模型控制器
        self.model_controller = ModelController(self.config_manager)
        self.logger.info("模型控制器初始化完成")

    def start(self) -> None:
        """优化的启动应用程序"""
        try:
            # 并行初始化
            self.initialize()

            # 启动自动启动模型（在后台线程中）
            auto_start_future = self.executor.submit(self._start_auto_start_models)

            # 并行启动核心服务
            def start_services():
                # 启动API服务器
                self.start_api_server()
                # 启动系统托盘服务
                self.start_tray_service()
                return "services"

            services_future = self.executor.submit(start_services)

            # 等待服务启动完成
            try:
                services_result = services_future.result(timeout=15)
                self.logger.info(f"{services_result} 启动完成")
            except concurrent.futures.TimeoutError:
                self.logger.error("服务启动超时")
                raise

            # 等待自动启动模型完成（不阻塞主线程）
            def check_auto_start():
                try:
                    auto_start_future.result(timeout=60)
                    self.logger.info("自动启动模型完成")
                except concurrent.futures.TimeoutError:
                    self.logger.warning("自动启动模型超时")

            check_thread = threading.Thread(target=check_auto_start, daemon=True)
            check_thread.start()

            self.running = True
            self.startup_complete.set()
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
        """优化的关闭应用程序 - 快速并行关闭"""
        if not self.running:
            return

        self.logger.info("正在快速关闭应用程序...")
        self.running = False
        self.shutdown_event.set()

        try:
            # 并行关闭各个组件
            def stop_monitor_thread():
                if self.monitor_thread and self.monitor_thread.is_alive():
                    self.stop_monitor = True
                    self.monitor_thread.join(timeout=3)
                    return "monitor_thread"
                return "monitor_thread_stopped"

            def close_monitor():
                if self.monitor:
                    try:
                        end_time = time.time()
                        self.monitor.update_program_runtime_end(end_time)
                        self.logger.debug(f"已更新程序结束时间戳: {end_time}")
                        self.monitor.close()
                        return "monitor"
                    except Exception as e:
                        self.logger.error(f"关闭监控器失败: {e}")
                        return "monitor_failed"
                return "monitor_none"

            def cleanup_processes():
                try:
                    cleanup_process_manager()
                    return "process_manager"
                except Exception as e:
                    self.logger.error(f"清理进程管理器失败: {e}")
                    return "process_manager_failed"

            def shutdown_model_controller():
                if self.model_controller:
                    try:
                        self.model_controller.shutdown()
                        return "model_controller"
                    except Exception as e:
                        self.logger.error(f"关闭模型控制器失败: {e}")
                        return "model_controller_failed"
                return "model_controller_none"

            # 提交关闭任务
            shutdown_tasks = [
                self.executor.submit(stop_monitor_thread),
                self.executor.submit(close_monitor),
                self.executor.submit(cleanup_processes),
                self.executor.submit(shutdown_model_controller)
            ]

            # 等待关闭任务完成，设置总超时
            timeout = 10  # 总超时10秒
            completed = []
            for future in concurrent.futures.as_completed(shutdown_tasks, timeout=timeout):
                try:
                    result = future.result()
                    completed.append(result)
                    self.logger.debug(f"{result} 关闭完成")
                except Exception as e:
                    self.logger.error(f"关闭任务失败: {e}")

            self.logger.info(f"关闭完成: {completed}/{len(shutdown_tasks)}")

            # 关闭线程池
            self.executor.shutdown(wait=True)

        except Exception as e:
            self.logger.error(f"关闭应用程序时发生错误: {e}")
        finally:
            self.logger.info("应用程序已退出")

    def _start_auto_start_models(self):
        """启动自动启动模型"""
        if self.model_controller:
            try:
                self.model_controller.start_auto_start_models()
            except Exception as e:
                self.logger.error(f"启动自动启动模型失败: {e}")

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