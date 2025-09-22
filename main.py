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
from core.model_controller import ModelController
from core.api_server import run_api_server
# from core.webui import run_web_ui  # WebUI 模块已删除，待重构

# 全局变量
model_controller: ModelController = None
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
    global model_controller, tray_service

    logger.info("正在启动核心服务...")

    try:
        # 初始化模型控制器
        config_path = 'config.json'
        if not os.path.exists(config_path):
            logger.error(f"配置文件不存在: {config_path}")
            sys.exit(1)

        model_controller = ModelController(config_path)
        logger.info("模型控制器初始化完成")

        # 启动API服务器
        api_cfg = model_controller.config['program']
        api_thread = threading.Thread(
            target=run_api_server,
            args=(model_controller, api_cfg['openai_host'], api_cfg['openai_port']),
            daemon=True
        )
        api_thread.start()
        threads.append(api_thread)
        logger.info(f"API服务器已启动: http://{api_cfg['openai_host']}:{api_cfg['openai_port']}")

        # WebUI 服务器已删除，待重构
        logger.info("WebUI 服务器已暂时移除，等待重构")

        # 等待服务启动
        time.sleep(3)

        # 启动自动启动的模型
        logger.info("检查需要自动启动的模型...")
        for primary_name in model_controller.models_state.keys():
            config = model_controller.get_model_config(primary_name)
            if config and config.get("auto_start", False):
                logger.info(f"正在自动启动模型: {primary_name}")
                threading.Thread(
                    target=model_controller.start_model,
                    args=(primary_name,),
                    daemon=True
                ).start()

        # 更新托盘状态
        if tray_service:
            tray_service.set_services_started(True)

        logger.info("核心服务启动完成")

    except Exception as e:
        logger.error(f"启动核心服务失败: {e}", exc_info=True)
        shutdown_application()
        sys.exit(1)

def start_tray_service():
    """启动系统托盘服务"""
    global tray_service

    try:
        from core.tray import SystemTray

        tray_service = SystemTray(model_controller)
        tray_service.set_exit_callback(shutdown_application)

        def tray_thread_func():
            try:
                tray_service.setup_tray_icon()
            except Exception as e:
                logger.error(f"托盘服务运行失败: {e}")
                shutdown_application()

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