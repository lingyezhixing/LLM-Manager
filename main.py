#!/usr/bin/env python3
"""
LLM-Manager 主程序入口
重构版本 - 采用插件化架构
"""

import threading
import time
import logging
import sys
import os
from utils.logger import setup_logging, get_logger
from core.config_manager import ConfigManager
from core.model_controller import ModelController
from core.openai_api_router import run_api_server
# from core.webui import run_web_ui  # WebUI 模块已删除，待重构

CONFIG_PATH = 'config.json'

# 全局变量
config_manager = None
model_controller = None
tray_service = None
threads = []

def setup_signal_handlers():
    """设置信号处理器"""
    try:
        import signal
        def signal_handler(signum, frame):
            logger.info(f"接收到信号 {signum}，正在关闭应用...")
            shutdown_application()
            # 给一些时间让清理完成
            time.sleep(1)
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)
    except ImportError:
        # Windows系统可能不支持signal模块
        pass

def start_core_services():
    """启动核心服务"""
    global config_manager, model_controller, tray_service

    logger.info("正在启动核心服务...")

    try:
        # 初始化配置管理器
        if not os.path.exists(CONFIG_PATH):
            logger.error(f"配置文件不存在: {CONFIG_PATH}")
            sys.exit(1)

        config_manager = ConfigManager(CONFIG_PATH)
        logger.info("配置管理器初始化完成")

        # 初始化模型控制器
        model_controller = ModelController(config_manager)
        logger.info("模型控制器初始化完成")

        # 启动API服务器
        api_cfg = config_manager.get_openai_config()
        api_thread = threading.Thread(
            target=run_api_server,
            args=(model_controller, api_cfg['host'], api_cfg['port']),
            daemon=True
        )
        api_thread.start()
        threads.append(api_thread)
        logger.info(f"API服务器已启动: http://{api_cfg['host']}:{api_cfg['port']}")

        # WebUI 服务器已删除，待重构
        logger.info("WebUI 服务器已暂时移除，等待重构")

        # 等待服务启动
        time.sleep(3)

        # 启动自动启动的模型
        logger.info("检查需要自动启动的模型...")
        for primary_name in config_manager.get_model_names():
            if config_manager.is_auto_start(primary_name):
                logger.info(f"正在自动启动模型: {primary_name}")
                threading.Thread(
                    target=model_controller.start_model,
                    args=(primary_name,),
                    daemon=True
                ).start()

        # 更新托盘状态
        if tray_service:
            # 托盘服务不再需要set_services_started方法
            logger.info("系统托盘服务已就绪")

        logger.info("核心服务启动完成")

    except Exception as e:
        logger.error(f"启动核心服务失败: {e}", exc_info=True)
        shutdown_application()
        sys.exit(1)

def start_tray_service():
    """启动系统托盘服务"""
    global tray_service, config_manager

    try:
        from core.tray import SystemTray

        # 获取API配置
        if config_manager is None:
            logger.error("配置管理器未初始化，无法启动托盘服务")
            return

        api_cfg = config_manager.get_openai_config()
        api_host = api_cfg['host']
        api_port = api_cfg['port']

        # 如果配置为0.0.0.0，使用localhost进行本地访问
        if api_host == '0.0.0.0':
            api_host = 'localhost'

        logger.info(f"托盘服务连接到API: {api_host}:{api_port}")
        tray_service = SystemTray(api_host, api_port)
        tray_service.set_exit_callback(shutdown_application)

        def tray_thread_func():
            try:
                tray_service.setup_tray_icon()
            except Exception as e:
                logger.error(f"托盘服务运行失败: {e}")
                shutdown_application()

        # 注意：托盘线程不能设置为daemon=True，否则程序会立即退出
        tray_thread = threading.Thread(target=tray_thread_func, daemon=False)
        tray_thread.start()
        threads.append(tray_thread)

        logger.info("系统托盘服务已启动")

    except Exception as e:
        logger.error(f"启动托盘服务失败: {e}")

def shutdown_application():
    """快速关闭应用程序"""
    logger.info("正在快速关闭应用程序...")

    # 使用快速关闭方式强制停止所有模型
    if model_controller:
        try:
            model_controller.shutdown()
        except Exception as e:
            logger.error(f"快速关闭模型控制器失败: {e}")

    # 不等待线程结束，直接退出
    logger.info("应用程序已快速关闭")

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
            shutdown_application()

    except Exception as e:
        print(f"致命错误: {e}")
        if 'logger' in locals():
            logger.error(f"应用程序启动失败: {e}", exc_info=True)
        sys.exit(1)

if __name__ == "__main__":
    main()