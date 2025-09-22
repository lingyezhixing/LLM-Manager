import subprocess
import time
import threading
import logging
import os
from typing import Dict, List, Tuple, Optional, Any, Set
from enum import Enum
from utils.logger import get_logger
from plugins.devices.Base_Class import DevicePlugin
from plugins.interfaces.Base_Class import InterfacePlugin
from .plugin_system import PluginManager
from .config_manager import ConfigManager
from .process_manager import get_process_manager

logger = get_logger(__name__)

class ModelStatus(Enum):
    """模型状态枚举"""
    STOPPED = "stopped"
    STARTING = "starting"
    INIT_SCRIPT = "init_script"
    HEALTH_CHECK = "health_check"
    ROUTING = "routing"
    FAILED = "failed"

class ModelController:
    """模型控制器 - 负责模型的启动、停止和资源管理"""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.device_plugins: Dict[str, DevicePlugin] = {}
        self.interface_plugins: Dict[str, InterfacePlugin] = {}
        self.models_state: Dict[str, Dict[str, Any]] = {}
        self.loading_lock = threading.Lock()
        self.is_running = True
        self.plugin_manager: Optional[PluginManager] = None
        self.process_manager = get_process_manager()

        # 启动空闲检查线程
        self.idle_check_thread = threading.Thread(target=self.idle_check_loop, daemon=True)
        self.idle_check_thread.start()

        self._init_model_states()
        self.load_plugins()

    def load_config(self):
        """重新加载配置文件"""
        self.config_manager.reload_config()
        self._init_model_states()
        logger.info("配置文件重新加载完成")

    def _init_model_states(self):
        """初始化模型状态"""
        new_states = {}
        model_names = self.config_manager.get_model_names()

        for primary_name in model_names:
            if primary_name in self.models_state:
                new_states[primary_name] = self.models_state[primary_name]
            else:
                new_states[primary_name] = {
                    "process": None,
                    "status": ModelStatus.STOPPED.value,
                    "last_access": None,
                    "pid": None,
                    "pending_requests": 0,
                    "lock": threading.Lock(),
                    "output_log": [],
                    "log_thread": None,
                    "current_config": None,
                    "failure_reason": None
                }

        self.models_state = new_states

    def load_plugins(self):
        """加载设备插件和接口插件"""
        # 从配置管理器获取插件目录
        device_dir = self.config_manager.get_device_plugin_dir()
        interface_dir = self.config_manager.get_interface_plugin_dir()

        # 创建插件管理器
        self.plugin_manager = PluginManager(device_dir, interface_dir)

        # 加载所有插件
        try:
            result = self.plugin_manager.load_all_plugins(model_manager=self)
            self.device_plugins = self.plugin_manager.get_all_device_plugins()
            self.interface_plugins = self.plugin_manager.get_all_interface_plugins()

            logger.info(f"设备插件自动加载完成: {list(self.device_plugins.keys())}")
            logger.info(f"接口插件自动加载完成: {list(self.interface_plugins.keys())}")

            # 检查是否有设备在线
            online_devices = [name for name, plugin in self.device_plugins.items() if plugin.is_online()]
            if online_devices:
                logger.info(f"在线设备: {online_devices}")
            else:
                logger.warning("未检测到在线设备")

        except Exception as e:
            logger.error(f"自动加载插件失败: {e}")

    def start_auto_start_models(self):
        """启动所有标记为自动启动的模型"""
        logger.info("检查需要自动启动的模型...")

        # 检查设备在线状态
        online_devices = [name for name, plugin in self.device_plugins.items() if plugin.is_online()]
        if not online_devices:
            logger.warning("没有在线设备，跳过自动启动模型")
            return

        started_models = []

        for primary_name in self.config_manager.get_model_names():
            if self.config_manager.is_auto_start(primary_name):
                logger.info(f"正在自动启动模型: {primary_name}")

                # 使用线程启动模型，避免阻塞初始化
                def start_model_thread(model_name):
                    try:
                        success, message = self.start_model(model_name)
                        if success:
                            logger.info(f"自动启动模型 {model_name} 成功")
                            started_models.append(model_name)
                        else:
                            logger.error(f"自动启动模型 {model_name} 失败: {message}")
                    except Exception as e:
                        logger.error(f"自动启动模型 {model_name} 时发生异常: {e}")

                # 使用daemon线程，这样程序退出时会自动终止
                thread = threading.Thread(
                    target=start_model_thread,
                    args=(primary_name,),
                    daemon=True
                )
                thread.start()

        if started_models:
            logger.info(f"成功启动了 {len(started_models)} 个自动启动模型: {started_models}")
        else:
            logger.info("没有需要自动启动的模型")

  
    
    def start_model(self, primary_name: str) -> Tuple[bool, str]:
        """启动模型 - 接受主名称"""
        state = self.models_state[primary_name]

        # 快速检查，避免不必要的全局锁等待
        with state['lock']:
            if state['status'] == ModelStatus.ROUTING.value:
                return True, f"模型 '{primary_name}' 已在运行"
            elif state['status'] == ModelStatus.STARTING.value:
                # 模型正在启动，等待启动完成
                pass

        # 使用全局加载锁确保一次只加载一个模型
        logger.info(f"请求加载模型 '{primary_name}'，正在等待全局加载锁...")
        with self.loading_lock:
            logger.info(f"已获得 '{primary_name}' 的全局加载锁，开始加载流程")

            # 双重检查：在等待锁之后，再次确认模型状态
            with state['lock']:
                if state['status'] == ModelStatus.ROUTING.value:
                    logger.info(f"模型 '{primary_name}' 在等待期间已被其他请求加载，跳过启动")
                    return True, f"模型 {primary_name} 已成功启动"
                elif state['status'] == ModelStatus.STARTING.value:
                    # 模型正在启动，等待启动完成
                    logger.info(f"模型 '{primary_name}' 正在启动中，等待完成...")
                    # 等待启动完成，最多等待5分钟
                    wait_start = time.time()
                    max_wait_time = 300
                    while state['status'] == ModelStatus.STARTING.value:
                        if time.time() - wait_start > max_wait_time:
                            state['lock'].release()
                            logger.error(f"等待模型 '{primary_name}' 启动超时")
                            return False, f"等待模型 '{primary_name}' 启动超时"
                        # 释放锁让其他线程可以修改状态，然后重新获取
                        state['lock'].release()
                        time.sleep(0.5)
                        state['lock'].acquire()

                    # 重新检查状态
                    if state['status'] == ModelStatus.ROUTING.value:
                        logger.info(f"模型 '{primary_name}' 启动完成")
                        return True, f"模型 {primary_name} 已成功启动"
                    else:
                        logger.error(f"模型 '{primary_name}' 启动失败")
                        return False, f"模型 {primary_name} 启动失败"

                # 确认由当前线程执行加载
                state['status'] = ModelStatus.STARTING.value
                state['output_log'].clear()
                state['output_log'].append(f"--- {time.ctime()} --- 启动模型 '{primary_name}'")

            try:
                return self._start_model_intelligent(primary_name)
            except Exception as e:
                with state['lock']:
                    state['status'] = ModelStatus.FAILED.value
                    state['failure_reason'] = str(e)
                logger.error(f"启动模型 {primary_name} 失败: {e}", exc_info=True)
                return False, f"启动模型 {primary_name} 失败: {e}"

    def _start_model_intelligent(self, primary_name: str) -> Tuple[bool, str]:
        """智能启动模型 - 包含设备检查和资源管理"""
        state = self.models_state[primary_name]

        # 获取自适应配置
        # 获取当前在线的设备
        online_devices = set()
        for device_name, device_plugin in self.device_plugins.items():
            if device_plugin.is_online():
                online_devices.add(device_name)

        model_config = self.config_manager.get_adaptive_model_config(primary_name, online_devices)
        if not model_config:
            current_devices = [name for name, plugin in self.device_plugins.items() if plugin.is_online()]
            error_msg = f"启动 '{primary_name}' 失败：没有适合当前设备状态 {current_devices} 的配置方案"
            state['status'] = ModelStatus.FAILED.value
            state['failure_reason'] = error_msg
            state['output_log'].append(f"[ERROR] {error_msg}")
            return False, error_msg

        state['current_config'] = model_config

        # 更新状态为启动脚本阶段
        with state['lock']:
            state['status'] = ModelStatus.INIT_SCRIPT.value

        # 检查设备资源
        if not self._check_and_free_resources(model_config):
            error_msg = f"启动 '{primary_name}' 失败：设备资源不足"
            state['status'] = ModelStatus.FAILED.value
            state['failure_reason'] = error_msg
            state['output_log'].append(f"[ERROR] {error_msg}")
            return False, error_msg

        # 启动模型
        logger.info(f"正在启动模型: {primary_name} (配置方案: {model_config.get('config_source', '默认')})")
        state['output_log'].append(f"[INFO] 使用配置方案: {model_config.get('config_source', '默认')}")
        state['output_log'].append(f"[INFO] 启动脚本: {model_config['bat_path']}")

        try:
            # 使用进程管理器启动模型进程
            project_root = os.path.dirname(os.path.abspath(self.config_manager.config_path))
            process_name = f"model_{primary_name}"

            success, message, pid = self.process_manager.start_process(
                name=process_name,
                command=model_config['bat_path'],
                cwd=project_root,
                description=f"模型进程: {primary_name}",
                shell=True,
                creation_flags=subprocess.CREATE_NEW_PROCESS_GROUP,
                capture_output=True
            )

            if not success:
                error_msg = f"启动模型进程失败: {message}"
                state['status'] = ModelStatus.FAILED.value
                state['failure_reason'] = error_msg
                return False, error_msg

            # 启动日志线程（从进程管理器获取输出流）
            process_info = self.process_manager.get_process_info(process_name)
            if process_info and process_info.get('process'):
                # 启动日志线程
                log_thread = threading.Thread(
                    target=self._log_process_output,
                    args=(process_info['process'].stdout, state['output_log']),
                    daemon=True
                )
                log_thread.start()
                state.update({
                    "process": process_info['process'],
                    "pid": pid,
                    "log_thread": log_thread
                })
            else:
                state.update({
                    "process": None,
                    "pid": pid,
                    "log_thread": None
                })

            # 执行健康检查
            return self._perform_health_checks(primary_name, model_config)

        except Exception as e:
            logger.error(f"启动模型进程失败: {e}")
            state['status'] = ModelStatus.FAILED.value
            state['failure_reason'] = str(e)
            return False, f"启动模型进程失败: {e}"

    def _check_and_free_resources(self, model_config: Dict[str, Any]) -> bool:
        """检查并释放设备资源"""
        required_memory = model_config.get("memory_mb", {})

        for attempt in range(3):  # 增加到3次尝试
            resource_ok = True
            deficit_devices = {}

            # 检查每个设备的内存
            for device_name, required_mb in required_memory.items():
                if device_name not in self.device_plugins:
                    logger.warning(f"配置中的设备 '{device_name}' 未找到插件，跳过")
                    continue

                device_plugin = self.device_plugins[device_name]
                if not device_plugin.is_online():
                    logger.warning(f"设备 '{device_name}' 不在线")
                    resource_ok = False
                    break

                device_info = device_plugin.get_devices_info()
                available_mb = device_info['available_memory_mb']
                if available_mb < required_mb:
                    deficit = required_mb - available_mb
                    deficit_devices[device_name] = deficit
                    resource_ok = False

            if resource_ok:
                logger.info("设备资源检查通过")
                return True

            logger.warning(f"设备资源不足，需要释放: {deficit_devices}")

            if attempt < 2:  # 前两次尝试都进行资源释放
                # 尝试停止空闲模型释放资源
                logger.info("尝试停止空闲模型以释放资源...")
                if self._stop_idle_models_for_resources(deficit_devices, model_config):
                    logger.info("资源释放完成，重新检查设备状态...")
                    time.sleep(3)  # 给系统一些时间来释放资源
                    # 继续下一次循环，重新检查实际可用资源
                else:
                    logger.warning("无法释放足够的资源")
                    break
            else:
                logger.error("达到最大尝试次数，仍然资源不足")
                break

        return False

    def _stop_idle_models_for_resources(self, deficit_devices: Dict[str, int], model_to_start: Dict[str, Any]) -> bool:
        """停止空闲模型以释放资源"""
        idle_candidates = []

        for name, state in self.models_state.items():
            with state['lock']:
                if (state['status'] == ModelStatus.ROUTING.value and
                    state.get('pending_requests', 0) == 0):
                    idle_candidates.append(name)

        # 按最后访问时间排序
        sorted_idle_models = sorted(
            idle_candidates,
            key=lambda m: self.models_state[m].get('last_access', 0) or 0
        )

        logger.info(f"找到 {len(sorted_idle_models)} 个空闲模型可供停止: {sorted_idle_models}")

        for model_name in sorted_idle_models:
            logger.info(f"为释放资源，正在停止空闲模型: {model_name}")
            success, message = self.stop_model(model_name)

            if success:
                logger.info(f"模型 {model_name} 已停止，等待系统释放资源...")
                time.sleep(2)  # 给系统一些时间来释放资源
                return True  # 返回True让主循环重新检查设备状态
            else:
                logger.warning(f"停止模型 {model_name} 失败: {message}")

        logger.warning("无法释放足够的资源")
        return False

    def _perform_health_checks(self, primary_name: str, model_config: Dict[str, Any]) -> Tuple[bool, str]:
        """执行健康检查"""
        state = self.models_state[primary_name]
        port = model_config['port']
        timeout_seconds = 300
        start_time = time.time()

        # 健康检查
        with state['lock']:
            state['status'] = ModelStatus.HEALTH_CHECK.value

        logger.info(f"正在对模型 '{primary_name}' 进行健康检查")

        model_mode = model_config.get("mode", "Chat")
        interface_plugin = self.interface_plugins.get(model_mode)

        if interface_plugin:
            # 使用插件的统一健康检查方法
            health_success, health_message = interface_plugin.health_check(primary_name, port, start_time, timeout_seconds)
            if health_success:
                logger.info(f"模型 '{primary_name}' 健康检查通过")
                with state['lock']:
                    state['status'] = ModelStatus.ROUTING.value
                    state['last_access'] = time.time()
                return True, f"模型 {primary_name} 启动成功"
            else:
                logger.error(f"模型 '{primary_name}' 健康检查失败: {health_message}")
                state['status'] = ModelStatus.FAILED.value
                state['failure_reason'] = health_message
                self.stop_model(primary_name)
                return False, f"健康检查失败: {health_message}"
        else:
            msg = f"未找到模式 '{model_mode}' 的接口插件，无法进行健康检查"
            logger.error(msg)
            state['status'] = ModelStatus.FAILED.value
            state['failure_reason'] = msg
            self.stop_model(primary_name)
            return False, msg

    def stop_model(self, primary_name: str) -> Tuple[bool, str]:
        """停止模型 - 接受主名称"""
        state = self.models_state[primary_name]

        with state['lock']:
            if state['status'] in [ModelStatus.STOPPED.value, ModelStatus.FAILED.value]:
                return True, f"模型 '{primary_name}' 已停止或失败"

            pid = state.get('pid')
            if pid:
                logger.info(f"正在强制停止模型 {primary_name} (PID: {pid})")
                # 使用进程管理器强制终止模型进程
                process_name = f"model_{primary_name}"
                success, message = self.process_manager.stop_process(process_name, force=True)

                if success:
                    logger.info(f"模型 {primary_name} (PID: {pid}) 已成功终止")
                else:
                    logger.warning(f"终止模型 {primary_name} PID:{pid} 失败: {message}")

            self._mark_model_as_stopped(primary_name, acquire_lock=False)
            return True, f"模型 {primary_name} 已停止"

    def _mark_model_as_stopped(self, primary_name: str, acquire_lock: bool = True):
        """标记模型为已停止状态"""
        state = self.models_state[primary_name]

        def update():
            state.update({
                "process": None,
                "pid": None,
                "status": ModelStatus.STOPPED.value,
                "last_access": None,
                "pending_requests": state.get('pending_requests', 0),
                "log_thread": None,
                "current_config": None,
                "failure_reason": None
            })

        if acquire_lock:
            with state['lock']:
                update()
        else:
            update()

    def unload_all_models(self):
        """卸载所有运行中的模型"""
        logger.info("正在卸载所有运行中的模型...")
        primary_names = list(self.models_state.keys())

        # 使用进程管理器批量停止所有模型进程
        terminated_models = []
        for name in primary_names:
            state = self.models_state[name]
            with state['lock']:
                if state['status'] not in [ModelStatus.STOPPED.value, ModelStatus.FAILED.value]:
                    process_name = f"model_{name}"
                    success, message = self.process_manager.stop_process(process_name, force=True)
                    if success:
                        terminated_models.append(name)

                    # 更新状态
                    state['status'] = ModelStatus.STOPPED.value
                    state['pid'] = None
                    state['process'] = None

        logger.info(f"所有模型均已卸载，共终止 {len(terminated_models)} 个模型进程")

    def increment_pending_requests(self, primary_name: str):
        """增加待处理请求计数 - 接受主名称"""
        state = self.models_state[primary_name]

        with state['lock']:
            state['pending_requests'] += 1
            logger.info(f"模型 {primary_name} 新请求进入，当前待处理: {state['pending_requests']}")

    def mark_request_completed(self, primary_name: str):
        """标记请求完成 - 接受主名称"""
        state = self.models_state[primary_name]

        with state['lock']:
            state['pending_requests'] = max(0, state['pending_requests'] - 1)
            state['last_access'] = time.time()
            logger.info(f"模型 {primary_name} 请求完成，剩余待处理: {state['pending_requests']}")

    def idle_check_loop(self):
        """空闲检查循环"""
        while self.is_running:
            time.sleep(30)
            if not self.is_running:
                break

            try:
                alive_time_min = self.config_manager.get_alive_time()
                if alive_time_min <= 0:
                    continue

                alive_time_sec = alive_time_min * 60
                now = time.time()

                for name in list(self.models_state.keys()):
                    state = self.models_state[name]
                    with state['lock']:
                        is_idle = (state['status'] == ModelStatus.ROUTING.value and
                                   state['last_access'] and
                                   state.get('pending_requests', 0) == 0)

                        if is_idle and (now - state['last_access']) > alive_time_sec:
                            logger.info(f"模型 {name} 空闲超过 {alive_time_min} 分钟，正在自动关闭...")
                            self.stop_model(name)

            except Exception as e:
                logger.error(f"空闲检查线程出错: {e}", exc_info=True)

    def get_all_models_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有模型状态"""
        status_copy = {}
        now = time.time()

        for primary_name, state in self.models_state.items():
            idle_seconds = (now - state['last_access']) if state.get('last_access') else -1
            config = self.config_manager.get_model_config(primary_name)

            # 获取当前在线的设备
            online_devices = set()
            for device_name, device_plugin in self.device_plugins.items():
                if device_plugin.is_online():
                    online_devices.add(device_name)
            adaptive_config = self.config_manager.get_adaptive_model_config(primary_name, online_devices)

            status_copy[primary_name] = {
                "aliases": config.get("aliases", [primary_name]) if config else [primary_name],
                "status": state['status'],
                "pid": state['pid'],
                "idle_time_sec": f"{idle_seconds:.0f}" if idle_seconds != -1 else "N/A",
                "pending_requests": state.get('pending_requests', 0),
                "mode": config.get("mode", "Chat") if config else "Chat",
                "is_available": bool(adaptive_config),
                "current_bat_path": adaptive_config.get("bat_path", "") if adaptive_config else "无可用配置",
                "config_source": adaptive_config.get("config_source", "N/A") if adaptive_config else "N/A",
                "failure_reason": state.get('failure_reason')
            }

        return status_copy

    def get_model_log(self, primary_name: str) -> List[str]:
        """获取模型日志"""
        if primary_name in self.models_state:
            return self.models_state[primary_name].get('output_log', [])
        return ["错误：未找到指定的模型"]

    def get_model_logs(self, primary_name: str) -> List[Dict[str, Any]]:
        """获取模型的结构化日志 - 接受主名称"""
        try:
            if primary_name not in self.models_state:
                return [{"timestamp": time.time(), "level": "error", "message": f"模型 '{primary_name}' 状态不存在"}]

            logs = self.models_state[primary_name].get('output_log', [])
            structured_logs = []

            for log_entry in logs:
                # 解析日志条目，假设格式为 "[时间] [级别] 消息"
                try:
                    # 简单的日志解析
                    if " - " in log_entry:
                        time_part, message_part = log_entry.split(" - ", 1)
                        timestamp = time.time()  # 如果无法解析时间，使用当前时间

                        # 确定日志级别
                        level = "info"
                        if any(word in message_part.lower() for word in ["error", "错误", "failed"]):
                            level = "error"
                        elif any(word in message_part.lower() for word in ["warning", "警告", "warn"]):
                            level = "warning"
                        elif any(word in message_part.lower() for word in ["success", "成功", "completed"]):
                            level = "success"

                        structured_logs.append({
                            "timestamp": timestamp,
                            "level": level,
                            "message": message_part
                        })
                    else:
                        structured_logs.append({
                            "timestamp": time.time(),
                            "level": "info",
                            "message": log_entry
                        })
                except Exception:
                    structured_logs.append({
                        "timestamp": time.time(),
                        "level": "info",
                        "message": log_entry
                    })

            return structured_logs[-100:]  # 返回最近100条日志
        except Exception as e:
            logger.error(f"获取模型日志失败: {e}")
            return [{"timestamp": time.time(), "level": "error", "message": f"获取日志失败: {str(e)}"}]

    def get_model_list(self) -> Dict[str, Any]:
        """获取模型列表"""
        data = []
        for primary_name in self.models_state.keys():
            config = self.config_manager.get_model_config(primary_name)
            if config:
                data.append({
                    "id": primary_name,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "user",
                    "aliases": config.get("aliases", []),
                    "mode": config.get("mode", "Chat")
                })

        return {"object": "list", "data": data}

    def _log_process_output(self, stream, log_list):
        """记录进程输出"""
        try:
            for line in iter(stream.readline, ''):
                log_list.append(line.strip())
                if len(log_list) > 200:  # 保持最近200行日志
                    log_list.pop(0)
        finally:
            stream.close()

    def shutdown(self):
        """关闭模型控制器 - 快速强制终止所有进程"""
        logger.info("正在快速关闭模型控制器...")
        self.is_running = False

        # 使用进程管理器强制终止所有模型进程
        terminated_models = []
        for primary_name, state in self.models_state.items():
            with state['lock']:
                pid = state.get('pid')
                if pid:
                    process_name = f"model_{primary_name}"
                    success, message = self.process_manager.stop_process(process_name, force=True)
                    if success:
                        terminated_models.append(primary_name)

                    # 更新状态
                    state['pid'] = None
                    state['status'] = ModelStatus.STOPPED.value
                    state['process'] = None

        logger.info(f"快速关闭完成，已终止 {len(terminated_models)} 个模型进程")