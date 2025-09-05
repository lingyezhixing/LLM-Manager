# main.py
import threading
import time
import logging
import sys
import webbrowser
import os
from PIL import Image
from pystray import Icon as TrayIcon, Menu as TrayMenu, MenuItem as TrayMenuItem
# Import project modules
from gpu_utils import get_gpu_info, simplify_gpu_name
# 模块导入可能在服务启动前发生，所以使用 try-except
try:
    from model_manager import ModelManager
    from api_server import run_api_server
    from web_ui import run_web_ui
except ImportError as e:
    # 这是一个兜底，理论上不应该发生
    logging.error(f"核心模块导入失败: {e}")
    sys.exit(1)

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')
logger = logging.getLogger(__name__)

# --- Global Variables ---
model_manager: ModelManager | None = None
tray_icon: TrayIcon | None = None
threads = []

# --- Tray Icon Functions (增强健壮性) ---
def check_services_started():
    """检查核心服务是否已启动"""
    if model_manager is None:
        logger.warning("核心服务正在等待GPU就绪，请稍后重试。")
        return False
    return True

def open_webui():
    """Opens the WebUI in the default browser."""
    if not check_services_started():
        return
    webui_host = model_manager.config['program']['webui_host']
    if webui_host == '0.0.0.0':
        webui_host = '127.0.0.1'
    port = model_manager.config['program']['webui_port']
    url = f"http://{webui_host}:{port}"
    logger.info(f"正在打开 WebUI: {url}")
    webbrowser.open(url)

def restart_auto_start_models():
    """Stops all running models and starts the ones marked for auto-start."""
    if not check_services_started():
        return
    logger.info("正在执行指令：重启所有 'auto_start' 模型...")
    model_manager.unload_all_models()
    time.sleep(3) # 等待模型完全卸载
    # 使用主名称启动
    for primary_name in model_manager.models_state.keys():
        config = model_manager.get_model_config(primary_name)
        if config and config.get("auto_start", False):
            logger.info(f"正在自动启动模型: {primary_name}")
            threading.Thread(target=model_manager.start_model, args=(primary_name,), daemon=True).start()

def unload_all_models():
    """Stops/unloads all currently running models."""
    if not check_services_started():
        return
    logger.info("正在执行指令：卸载全部模型...")
    model_manager.unload_all_models()
    logger.info("全部模型卸载完毕。")

def exit_application():
    """Shuts down all services and exits the application."""
    logger.info("正在退出应用程序...")
    if tray_icon:
        tray_icon.stop()
    if model_manager: 
        model_manager.shutdown()
    os._exit(0)

def setup_tray_icon():
    """Creates and runs the system tray icon. This is a blocking call."""
    global tray_icon
    try:
        icon_path = os.path.join(os.path.dirname(__file__), 'icons', 'icon.ico')
        if not os.path.exists(icon_path):
             logger.error(f"图标文件未找到: {icon_path}。将使用默认图标。")
             image = Image.new('RGB', (64, 64), 'black')
        else:
            image = Image.open(icon_path)
        
        menu = TrayMenu(
            TrayMenuItem('打开 WebUI', open_webui, default=True),
            TrayMenu.SEPARATOR,
            TrayMenuItem('重启 Auto-Start 模型', restart_auto_start_models),
            TrayMenuItem('卸载全部模型', unload_all_models),
            TrayMenu.SEPARATOR,
            TrayMenuItem('退出', exit_application)
        )
        tray_icon = TrayIcon("LLM-Manager", image, "LLM-Manager (等待GPU...)", menu)
        logger.info("系统托盘图标已创建。")
        tray_icon.run() # 此处会阻塞主线程，保持程序运行
    except Exception as e:
        logger.error(f"创建系统托盘图标失败: {e}")
        exit_application()

# --- Main Application Logic ---
def conditional_start_services():
    """
    一个在后台运行的函数，它会循环监测GPU，
    直到满足条件后才启动所有核心服务。
    """
    global model_manager 
    
    required_gpus = {"rtx 4060", "v100"}
    logger.info(f"程序启动需要以下GPU同时存在: {', '.join(required_gpus)}")
    
    while True:
        gpus_info = get_gpu_info()
        detected_gpus_simple_names = {gpu.simple_name for gpu in gpus_info}
        
        if required_gpus.issubset(detected_gpus_simple_names):
            logger.info("所有必需的GPU均已检测到！准备启动核心服务...")
            if tray_icon:
                tray_icon.title = "LLM-Manager" # 更新托盘提示文字
            break 
        else:
            found = required_gpus.intersection(detected_gpus_simple_names)
            missing = required_gpus.difference(detected_gpus_simple_names)
            logger.info(f"等待GPU就绪... (已找到: {list(found) if found else '无'}, 仍需: {list(missing)}). 5秒后重试...")
            time.sleep(5)
            
    try:
        model_manager = ModelManager('config.json')
    except Exception as e:
        logger.error(f"加载配置或初始化模型管理器时出错: {e}")
        exit_application()
    
    api_cfg = model_manager.config['program']
    api_thread = threading.Thread(
        target=run_api_server,
        args=(model_manager, api_cfg['openai_host'], api_cfg['openai_port']),
        daemon=True
    )
    api_thread.start()
    threads.append(api_thread)
    
    webui_cfg = model_manager.config['program']
    webui_thread = threading.Thread(
        target=run_web_ui,
        args=(model_manager, webui_cfg['webui_host'], webui_cfg['webui_port']),
        daemon=True
    )
    webui_thread.start()
    threads.append(webui_thread)
    
    time.sleep(2)
    logger.info("检查需要自动启动的模型...")
    for primary_name in model_manager.models_state.keys():
        config = model_manager.get_model_config(primary_name)
        if config and config.get("auto_start", False):
            threading.Thread(target=model_manager.start_model, args=(primary_name,), daemon=True).start()

if __name__ == "__main__":
    logger.info("LLM-Manager 启动中...")
    
    services_thread = threading.Thread(target=conditional_start_services, daemon=True)
    services_thread.start()
    
    setup_tray_icon()
    
    logger.info("LLM-Manager 已关闭。")