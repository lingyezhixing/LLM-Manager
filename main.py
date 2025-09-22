#!/usr/bin/env python3
"""
LLM-Manager 主程序入口
重构版本 - 架构简化，只负责启动API服务器
"""

import threading
import time
import logging
import sys
import os
from utils.logger import setup_logging, get_logger
from core.config_manager import ConfigManager
from core.openai_api_router import run_api_server
from core.process_manager import get_process_manager, cleanup_process_manager

CONFIG_PATH = 'config.json'

# 全局变量
config_manager = None
tray_service = None
threads = []

def setup_signal_handlers():
    """设置信号处理器"""
    try:
        import signal
        def signal_handler(signum, frame):
            logger.info(f"接收到信号 {signum}，正在关闭应用...")
            # 清理进程管理器
            process_manager = get_process_manager()
            process_manager.cleanup()
            # 通过托盘服务关闭应用
            if tray_service:
                tray_service.exit_application()
            else:
                logger.warning("托盘服务未启动，直接退出")
                sys.exit(0)
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    except ImportError:
        # Windows系统可能不支持signal模块
        pass

def start_core_services():
    """启动核心服务 - 只启动API服务器"""
    global config_manager

    logger.info("正在启动核心服务...")

    try:
        # 初始化配置管理器
        if not os.path.exists(CONFIG_PATH):
            logger.error(f"配置文件不存在: {CONFIG_PATH}")
            sys.exit(1)

        config_manager = ConfigManager(CONFIG_PATH)
        logger.info("配置管理器初始化完成")

        # 初始化进程管理器
        process_manager = get_process_manager()
        logger.info("进程管理器初始化完成")

        # 启动API服务器，传递配置管理器实例
        api_thread = threading.Thread(
            target=run_api_server,
            args=(config_manager,),
            daemon=False  # 不能设置为daemon，否则程序会立即退出
        )
        api_thread.start()
        threads.append(api_thread)

        logger.info("核心服务启动完成")

    except Exception as e:
        logger.error(f"启动核心服务失败: {e}", exc_info=True)
        sys.exit(1)

def start_tray_service():
    """启动系统托盘服务"""
    global tray_service, config_manager

    try:
        from core.tray import SystemTray

        # 检查配置管理器
        if config_manager is None:
            logger.error("配置管理器未初始化，无法启动托盘服务")
            return

        logger.info("启动托盘服务，传递配置管理器实例")
        tray_service = SystemTray(config_manager)

        def tray_thread_func():
            try:
                tray_service.start_tray()
            except Exception as e:
                logger.error(f"托盘服务运行失败: {e}")
                # 托盘服务失败时直接退出
                logger.info("托盘服务失败，应用程序退出")
                sys.exit(1)

        # 注意：托盘线程不能设置为daemon=True，否则程序会立即退出
        tray_thread = threading.Thread(target=tray_thread_func, daemon=False)
        tray_thread.start()
        threads.append(tray_thread)

        logger.info("系统托盘服务已启动")

    except Exception as e:
        logger.error(f"启动托盘服务失败: {e}")


def main():
    """主函数"""
    global logger

    try:
        # 设置日志
        log_level = os.environ.get('LOG_LEVEL', 'INFO')
        setup_logging(log_level=log_level)
        logger = get_logger(__name__)

        logger.info("LLM-Manager 启动中...")
        logger.info(f"Python 版本: {sys.version}")
        logger.info(f"工作目录: {os.getcwd()}")

        # 设置信号处理器
        setup_signal_handlers()

        # 启动核心服务
        start_core_services()

        # 启动系统托盘服务
        start_tray_service()

        # 主循环
        logger.info("LLM-Manager 运行中，按 Ctrl+C 退出...")
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("接收到键盘中断信号")
        finally:
            # 清理进程管理器
            try:
                cleanup_process_manager()
                logger.info("进程管理器已清理")
            except Exception as e:
                logger.error(f"清理进程管理器失败: {e}")
            # 通过托盘服务关闭应用
            if tray_service:
                tray_service.exit_application()
            else:
                logger.warning("托盘服务未启动，直接退出")

    except Exception as e:
        print(f"致命错误: {e}")
        if 'logger' in locals():
            logger.error(f"应用程序启动失败: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()