import logging
import webbrowser
import os
from PIL import Image
from pystray import Icon as TrayIcon, Menu as TrayMenu, MenuItem as TrayMenuItem
from typing import Optional
from utils.logger import get_logger
from core.model_controller import ModelController

logger = get_logger(__name__)

class SystemTray:
    """系统托盘服务"""

    def __init__(self, model_controller: ModelController):
        self.model_controller = model_controller
        self.tray_icon: Optional[TrayIcon] = None
        self.exit_callback = None

    def open_webui(self):
        """打开WebUI"""
        logger.info("WebUI 已暂时移除，等待重构")
        logger.info("可通过 API 服务器访问: http://127.0.0.1:8000")
        # WebUI 重构后此处需要更新

    def restart_auto_start_models(self):
        """重启所有auto_start模型"""
        logger.info("正在执行指令：重启所有 'auto_start' 模型...")
        self.model_controller.unload_all_models()
        time.sleep(3)

        for primary_name in self.model_controller.models_state.keys():
            config = self.model_controller.get_model_config(primary_name)
            if config and config.get("auto_start", False):
                logger.info(f"正在自动启动模型: {primary_name}")
                import threading
                threading.Thread(
                    target=self.model_controller.start_model,
                    args=(primary_name,),
                    daemon=True
                ).start()

    def unload_all_models(self):
        """卸载全部模型"""
        logger.info("正在执行指令：卸载全部模型...")
        self.model_controller.unload_all_models()
        logger.info("全部模型卸载完毕。")

    def get_tray_title(self) -> str:
        """获取托盘标题"""
        online_devices = []
        for device_name, device_plugin in self.model_controller.device_plugins.items():
            if device_plugin.is_online():
                online_devices.append(device_name)

        if online_devices:
            return f"LLM-Manager (设备: {', '.join(online_devices)})"
        else:
            return "LLM-Manager (无在线设备)"

    def exit_application(self):
        """退出应用程序 - 快速强制退出"""
        logger.info("正在快速退出应用程序...")

        # 立即强制关闭所有模型进程
        if self.model_controller:
            logger.info("正在强制关闭所有模型...")
            self.model_controller.shutdown()

        if self.exit_callback:
            logger.info("调用退出回调...")
            try:
                self.exit_callback()
            except Exception as e:
                logger.error(f"退出回调执行失败: {e}")

        logger.info("强制退出程序")
        os._exit(0)

    def setup_tray_icon(self):
        """创建并运行系统托盘图标"""
        try:
            icon_path = os.path.join(os.path.dirname(__file__), '..', 'icons', 'icon.ico')
            if not os.path.exists(icon_path):
                logger.error(f"图标文件未找到: {icon_path}。将使用默认图标。")
                image = Image.new('RGB', (64, 64), 'black')
            else:
                image = Image.open(icon_path)

            menu = TrayMenu(
                TrayMenuItem('打开 WebUI', self.open_webui, default=True),
                TrayMenu.SEPARATOR,
                TrayMenuItem('重启 Auto-Start 模型', self.restart_auto_start_models),
                TrayMenuItem('卸载全部模型', self.unload_all_models),
                TrayMenu.SEPARATOR,
                TrayMenuItem('退出', self.exit_application)
            )

            self.tray_icon = TrayIcon(
                "LLM-Manager",
                image,
                self.get_tray_title(),
                menu
            )

            logger.info("系统托盘图标已创建。")
            self.tray_icon.run()

        except Exception as e:
            logger.error(f"创建系统托盘图标失败: {e}")
            self.exit_application()

    def set_exit_callback(self, callback):
        """设置退出回调函数"""
        self.exit_callback = callback

def run_system_tray(model_controller: ModelController):
    """运行系统托盘服务的便捷函数"""
    tray = SystemTray(model_controller)
    return tray