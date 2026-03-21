import os
import threading
import webbrowser
import time
import socket
from typing import Optional
from PIL import Image
from utils.logger import get_logger
from core.config_manager import ConfigManager

try:
    from pystray import Icon as TrayIcon, Menu as TrayMenu, MenuItem as TrayMenuItem
    PYSTRAY_AVAILABLE = True
except ImportError:
    PYSTRAY_AVAILABLE = False

logger = get_logger(__name__)

class SystemTray:
    def __init__(self, config_manager: ConfigManager, model_controller):
        self.config_manager = config_manager
        self.model_controller = model_controller

        server_cfg = self.config_manager.get_openai_config()
        self.server_host = 'localhost' if server_cfg['host'] == '0.0.0.0' else server_cfg['host']
        self.server_port = server_cfg['port']
        self.server_url = f"http://{self.server_host}:{self.server_port}"

        self.tray_icon: Optional[TrayIcon] = None
        self.exit_callback = None

        # 检查是否为无头模式 (Linux无桌面环境 或 缺少依赖)
        self.is_headless = self._check_headless()

        self._tray_lock = threading.Lock()
        self._tray_running = False

    def _check_headless(self) -> bool:
        if not PYSTRAY_AVAILABLE: return True
        if os.name == 'posix' and 'DISPLAY' not in os.environ: return True
        return False

    def get_online_devices(self) -> list:
        try:
            # 读取设备状态缓存
            devices = self.model_controller.plugin_manager.get_device_status_snapshot()
            return [name for name, info in devices.items() if info.get("online", False)]
        except Exception:
            return []

    def send_wol_packet(self, icon=None, item=None):
        """发送网络唤醒魔术包"""
        wol_config = self.config_manager.get_wol_config()
        if not wol_config:
            logger.warning("未找到网络唤醒配置")
            return
        
        broadcast_addr = wol_config.get("broadcast_address")
        mac_address = wol_config.get("mac_address")
        
        if not broadcast_addr or not mac_address:
            logger.warning("网络唤醒配置不完整")
            return
        
        # 清理 MAC 地址格式（移除冒号、短横线、空格）
        mac_clean = mac_address.replace(":", "").replace("-", "").replace(" ", "")
        mac_bytes = bytes.fromhex(mac_clean)
        
        # 构建魔术包：6 字节前缀 + 16 组 MAC 地址
        magic_packet = b'\xff' * 6 + mac_bytes * 16
        
        # 发送 UDP 广播
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(magic_packet, (broadcast_addr, 9))
            sock.close()
            logger.info(f"网络唤醒包已发送到 {broadcast_addr}")
        except Exception as e:
            logger.error(f"发送网络唤醒包失败：{e}")

    # ============ 鼠标悬停提示 (Tooltip) ============
    def _update_tooltip(self):
        """更新鼠标悬浮在图标上时显示的文字"""
        if not self.tray_icon: return

        devices = self.get_online_devices()
        if devices:
            self.tray_icon.title = f"在线设备: {', '.join(devices)}"
        else:
            self.tray_icon.title = "在线设备: (暂无)"

    # ============ 业务交互 ============
    def open_webui(self, icon=None, item=None):
        logger.info(f"正在浏览器中打开 WebUI: {self.server_url}")
        webbrowser.open(self.server_url)

    def restart_auto_start_models(self, icon=None, item=None):
        threading.Thread(target=self._async_restart_models, daemon=True).start()

    def _async_restart_models(self):
        logger.info("正在重启 auto_start 模型...")
        self.model_controller.unload_all_models()
        time.sleep(2)

        auto_start_models = [name for name in self.config_manager.get_model_names() if self.config_manager.is_auto_start(name)]
        for name in auto_start_models:
            self.model_controller.start_model(name)

        logger.info("重启指令执行完毕")
        self._update_tooltip()

    def unload_all_models_action(self, icon=None, item=None):
        self.model_controller.unload_all_models()
        self._update_tooltip()

    def exit_application(self, icon=None, item=None):
        logger.info("收到退出指令...")
        if self.tray_icon:
            self.tray_icon.stop()
        if self.exit_callback:
            self.exit_callback()
        os._exit(0)

    # ============ 核心启动逻辑 ============
    def _init_tray_ui(self):
        with self._tray_lock:
            if self._tray_running: return
            self._tray_running = True

        logger.info("正在初始化系统托盘 UI...")
        icon_path = os.path.join(os.path.dirname(__file__), '..', 'icons', 'icon.ico')

        if os.path.exists(icon_path):
            image = Image.open(icon_path)
        else:
            # 最后的防线：如果没有图标文件，给一个黑色方块防止报错
            image = Image.new('RGB', (64, 64), 'black')

        # 菜单布局
        menu = TrayMenu(
            # 1. 默认操作 (双击触发)
            TrayMenuItem('🌐 打开 WebUI', self.open_webui, default=True),
            TrayMenu.SEPARATOR,

            # 2. 网络唤醒
            TrayMenuItem('🔔 网络唤醒飞牛', self.send_wol_packet),
            TrayMenuItem('▶ 重启自启模型', self.restart_auto_start_models),
            TrayMenuItem('⏹ 卸载全部模型', self.unload_all_models_action),
            TrayMenu.SEPARATOR,

            # 3. 退出
            TrayMenuItem('❌ 退出程序', self.exit_application)
        )

        # 初始化图标对象
        self.tray_icon = TrayIcon(
            "LLM-Manager",
            image,
            "初始化中...",
            menu
        )

        # 立即刷新一次状态显示
        self._update_tooltip()

        logger.info("托盘 UI 已成功挂载！")
        self.tray_icon.run()

    def start_tray(self):
        """启动托盘服务"""
        if self.is_headless:
            logger.info("无头模式：后台静默运行中...")
            return

        threading.Thread(target=self._init_tray_ui, daemon=True, name="TrayUIThread").start()

    def shutdown(self):
        """关闭托盘资源"""
        if self.tray_icon:
            self.tray_icon.stop()

    def set_exit_callback(self, callback):
        self.exit_callback = callback
