"""
模型控制器 - 负责模型的启动、停止和资源管理
优化版本：支持并行启动和智能资源管理
"""

import subprocess
import time
import threading
import logging
import os
import concurrent.futures
import queue
from typing import Dict, List, Tuple, Optional, Any
from enum import Enum
from utils.logger import get_logger
from .plugin_system import PluginManager
from .config_manager import ConfigManager
from .process_manager import get_process_manager
from .monitor import Monitor

logger = get_logger(__name__)


class LogManager:
    """日志管理器 - 负责模型日志的收集、存储和流式推送"""

    def __init__(self):
        """初始化日志管理器"""
        self.model_logs: Dict[str, List[Dict[str, Any]]] = {}
        self.log_subscribers: Dict[str, List[queue.Queue]] = {}
        self.log_locks: Dict[str, threading.Lock] = {}
        self.global_lock = threading.Lock()
        self._running = True

        logger.info("日志管理器初始化完成")

    def register_model(self, model_name: str):
        """注册模型日志管理"""
        with self.global_lock:
            if model_name not in self.model_logs:
                self.model_logs[model_name] = []
                self.log_subscribers[model_name] = []
                self.log_locks[model_name] = threading.Lock()
                logger.debug(f"已注册模型日志管理: {model_name}")

    def unregister_model(self, model_name: str):
        """注销模型日志管理"""
        with self.global_lock:
            if model_name in self.model_logs:
                # 通知所有订阅者连接关闭
                for subscriber_queue in self.log_subscribers[model_name]:
                    try:
                        subscriber_queue.put(None)  # 发送结束信号
                    except:
                        pass

                del self.model_logs[model_name]
                del self.log_subscribers[model_name]
                del self.log_locks[model_name]
                logger.debug(f"已注销模型日志管理: {model_name}")

    def add_log_entry(self, model_name: str, message: str, level: str = "info"):
        """添加日志条目（系统日志，用于管理操作）"""
        if model_name not in self.model_logs:
            self.register_model(model_name)

        log_entry = {
            "timestamp": time.time(),
            "level": level,
            "message": message
        }

        model_lock = self.log_locks[model_name]
        with model_lock:
            self.model_logs[model_name].append(log_entry)

        # 通知订阅者
        self._notify_subscribers(model_name, log_entry)

    def add_raw_output(self, model_name: str, message: str):
        """添加原始输出（模型进程输出）"""
        log_entry = {
            "timestamp": time.time(),
            "message": message
        }

        if model_name not in self.model_logs:
            self.register_model(model_name)

        model_lock = self.log_locks[model_name]
        with model_lock:
            self.model_logs[model_name].append(log_entry)

        # 通知订阅者
        self._notify_subscribers(model_name, log_entry)

    def get_logs(self, model_name: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """获取模型日志"""
        if model_name not in self.model_logs:
            return []

        model_lock = self.log_locks[model_name]
        with model_lock:
            logs = self.model_logs[model_name].copy()

        if limit is not None:
            logs = logs[-limit:]

        return logs

    def get_all_logs(self) -> Dict[str, List[Dict[str, Any]]]:
        """获取所有模型日志"""
        with self.global_lock:
            result = {}
            for model_name in self.model_logs:
                result[model_name] = self.get_logs(model_name)
            return result

    def clear_logs(self, model_name: str):
        """清空模型日志"""
        if model_name in self.model_logs:
            model_lock = self.log_locks[model_name]
            with model_lock:
                self.model_logs[model_name].clear()
            logger.debug(f"已清空模型日志: {model_name}")

    def cleanup_old_logs(self, model_name: str, keep_minutes: int) -> int:
        """
        清理指定分钟数之前的日志

        Args:
            model_name: 模型名称
            keep_minutes: 保留最近多少分钟的日志

        Returns:
            删除的日志条目数量
        """
        if model_name not in self.model_logs:
            return 0

        current_time = time.time()
        cutoff_time = current_time - (keep_minutes * 60)

        model_lock = self.log_locks[model_name]
        with model_lock:
            original_count = len(self.model_logs[model_name])

            # 保留在截止时间之后的日志
            self.model_logs[model_name] = [
                log for log in self.model_logs[model_name]
                if log['timestamp'] > cutoff_time
            ]

            removed_count = original_count - len(self.model_logs[model_name])

        if removed_count > 0:
            logger.debug(f"已清理模型 '{model_name}' {keep_minutes} 分钟前的日志，删除 {removed_count} 条")

        return removed_count

    def subscribe_to_logs(self, model_name: str) -> queue.Queue:
        """订阅模型日志流"""
        if model_name not in self.model_logs:
            self.register_model(model_name)

        subscriber_queue = queue.Queue()

        with self.global_lock:
            self.log_subscribers[model_name].append(subscriber_queue)

        logger.debug(f"新订阅者加入模型日志流: {model_name}")
        return subscriber_queue

    def unsubscribe_from_logs(self, model_name: str, subscriber_queue: queue.Queue):
        """取消订阅模型日志流"""
        if model_name in self.log_subscribers:
            with self.global_lock:
                if subscriber_queue in self.log_subscribers[model_name]:
                    self.log_subscribers[model_name].remove(subscriber_queue)
                    logger.debug(f"订阅者离开模型日志流: {model_name}")

    def _notify_subscribers(self, model_name: str, log_entry: Dict[str, Any]):
        """通知所有订阅者新的日志条目"""
        if model_name not in self.log_subscribers:
            return

        # 创建副本以避免在迭代时修改列表
        subscribers_copy = self.log_subscribers[model_name].copy()

        for subscriber_queue in subscribers_copy:
            try:
                # 非阻塞方式发送，如果队列满则跳过
                subscriber_queue.put_nowait(log_entry)
            except queue.Full:
                # 订阅者队列已满，移除该订阅者
                with self.global_lock:
                    if subscriber_queue in self.log_subscribers[model_name]:
                        self.log_subscribers[model_name].remove(subscriber_queue)
                        logger.debug(f"移除已满的订阅者队列: {model_name}")
            except Exception:
                # 其他错误，移除订阅者
                with self.global_lock:
                    if subscriber_queue in self.log_subscribers[model_name]:
                        self.log_subscribers[model_name].remove(subscriber_queue)
                        logger.debug(f"移除异常的订阅者队列: {model_name}")

    def get_log_stats(self) -> Dict[str, Any]:
        """获取日志统计信息"""
        with self.global_lock:
            stats = {
                "total_models": len(self.model_logs),
                "total_log_entries": sum(len(logs) for logs in self.model_logs.values()),
                "total_subscribers": sum(len(subscribers) for subscribers in self.log_subscribers.values()),
                "model_stats": {}
            }

            for model_name, logs in self.model_logs.items():
                stats["model_stats"][model_name] = {
                    "log_count": len(logs),
                    "subscriber_count": len(self.log_subscribers[model_name])
                }

            return stats

    def shutdown(self):
        """关闭日志管理器"""
        self._running = False

        # 通知所有订阅者
        with self.global_lock:
            for model_name, subscribers in self.log_subscribers.items():
                for subscriber_queue in subscribers:
                    try:
                        subscriber_queue.put(None)  # 发送结束信号
                    except:
                        pass

        logger.info("日志管理器已关闭")


class ModelRuntimeMonitor:
    """模型运行时间监控器 - 负责记录模型运行时间到数据库"""

    def __init__(self, db_path: Optional[str] = None):
        """
        初始化模型运行时间监控器

        Args:
            db_path: 数据库路径，默认使用monitor的默认路径
        """
        self.monitor = Monitor(db_path)
        self.active_models: Dict[str, threading.Timer] = {}
        self.lock = threading.Lock()

        logger.info("模型运行时间监控器初始化完成")

    def record_model_start(self, model_name: str) -> bool:
        """
        记录模型启动时间并启动定时器

        Args:
            model_name: 模型名称

        Returns:
            是否成功记录
        """
        start_time = time.time()

        # 记录启动时间到数据库
        try:
            self.monitor.add_model_runtime_start(model_name, start_time)
            logger.info(f"已记录模型 '{model_name}' 启动时间: {start_time}")
        except Exception as e:
            logger.error(f"记录模型 '{model_name}' 启动时间失败: {e}")
            return False

        # 启动定时更新线程
        with self.lock:
            # 停止已有的定时器（如果有）
            if model_name in self.active_models:
                self.active_models[model_name].cancel()

            # 创建新的定时器，每10秒更新一次
            timer = threading.Timer(10.0, self._update_runtime_periodically, args=[model_name])
            timer.daemon = True
            timer.start()
            self.active_models[model_name] = timer

        logger.info(f"已启动模型 '{model_name}' 的运行时间定时更新器")
        return True

    def _update_runtime_periodically(self, model_name: str):
        """
        定期更新模型运行时间

        Args:
            model_name: 模型名称
        """
        with self.lock:
            # 如果模型仍在活动状态，继续定时更新
            if model_name in self.active_models:
                end_time = time.time()
                try:
                    self.monitor.update_model_runtime_end(model_name, end_time)
                    logger.debug(f"已更新模型 '{model_name}' 运行时间: {end_time}")
                except Exception as e:
                    logger.error(f"更新模型 '{model_name}' 运行时间失败: {e}")

                # 创建下一个定时器
                timer = threading.Timer(10.0, self._update_runtime_periodically, args=[model_name])
                timer.daemon = True
                timer.start()
                self.active_models[model_name] = timer

    def record_model_stop(self, model_name: str) -> bool:
        """
        记录模型停止时间并停止定时器

        Args:
            model_name: 模型名称

        Returns:
            是否成功记录
        """
        end_time = time.time()

        # 停止定时器
        with self.lock:
            if model_name in self.active_models:
                self.active_models[model_name].cancel()
                del self.active_models[model_name]

        # 记录最终停止时间到数据库
        try:
            self.monitor.update_model_runtime_end(model_name, end_time)
            logger.info(f"已记录模型 '{model_name}' 停止时间: {end_time}")
            return True
        except Exception as e:
            logger.error(f"记录模型 '{model_name}' 停止时间失败: {e}")
            return False

    def is_model_monitored(self, model_name: str) -> bool:
        """
        检查模型是否正在被监控

        Args:
            model_name: 模型名称

        Returns:
            是否正在监控
        """
        with self.lock:
            return model_name in self.active_models

    def get_active_models(self) -> List[str]:
        """
        获取正在被监控的模型列表

        Returns:
            正在监控的模型名称列表
        """
        with self.lock:
            return list(self.active_models.keys())

    def shutdown(self):
        """关闭监控器，停止所有定时器"""
        with self.lock:
            for model_name, timer in self.active_models.items():
                timer.cancel()
                logger.info(f"已停止模型 '{model_name}' 的运行时间监控")
            self.active_models.clear()

        try:
            self.monitor.close()
            logger.info("模型运行时间监控器已关闭")
        except Exception as e:
            logger.error(f"关闭模型运行时间监控器失败: {e}")

    def __del__(self):
        """析构函数"""
        try:
            self.shutdown()
        except Exception:
            pass


class ModelStatus(Enum):
    """模型状态枚举"""
    STOPPED = "stopped"
    STARTING = "starting"
    INIT_SCRIPT = "init_script"
    HEALTH_CHECK = "health_check"
    ROUTING = "routing"
    FAILED = "failed"


class ModelController:
    """优化的模型控制器 - 支持并行启动和智能资源管理"""

    def __init__(self, config_manager: ConfigManager):
        """
        初始化模型控制器

        Args:
            config_manager: 配置管理器实例
        """
        self.config_manager = config_manager
        self.models_state: Dict[str, Dict[str, Any]] = {}
        self.is_running = True
        self.plugin_manager: Optional[PluginManager] = None
        self.process_manager = get_process_manager()
        self.runtime_monitor = ModelRuntimeMonitor()
        self.log_manager = LogManager()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
        self.startup_futures: Dict[str, concurrent.futures.Future] = {}
        self.startup_locks: Dict[str, threading.Lock] = {}  # 每个模型的专用锁
        self.shutdown_event = threading.Event()

        # 启动空闲检查线程
        self.idle_check_thread = threading.Thread(target=self.idle_check_loop, daemon=True)
        self.idle_check_thread.start()

        # 初始化模型状态
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
                    "lock": threading.Lock(),
                    "log_thread": None,
                    "current_config": None,
                    "failure_reason": None
                }
                # 为每个模型创建专用的启动锁
                self.startup_locks[primary_name] = threading.Lock()
                # 注册模型到日志管理器
                self.log_manager.register_model(primary_name)

        self.models_state = new_states
        self.load_plugins()

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
            logger.info(f"设备插件自动加载完成: {list(self.plugin_manager.get_all_device_plugins().keys())}")
            logger.info(f"接口插件自动加载完成: {list(self.plugin_manager.get_all_interface_plugins().keys())}")

            # 检查是否有设备在线
            online_devices = [name for name, plugin in self.plugin_manager.get_all_device_plugins().items() if plugin.is_online()]
            if online_devices:
                logger.info(f"在线设备: {online_devices}")
            else:
                logger.warning("未检测到在线设备")

        except Exception as e:
            logger.error(f"自动加载插件失败: {e}")

    def start_auto_start_models(self):
        """优化的启动所有标记为自动启动的模型 - 支持并行启动"""
        logger.info("检查需要自动启动的模型...")

        # 检查设备在线状态
        online_devices = [name for name, plugin in self.plugin_manager.get_all_device_plugins().items() if plugin.is_online()]
        if not online_devices:
            logger.warning("没有在线设备，跳过自动启动模型")
            return

        auto_start_models = [
            primary_name for primary_name in self.config_manager.get_model_names()
            if self.config_manager.is_auto_start(primary_name)
        ]

        if not auto_start_models:
            logger.info("没有需要自动启动的模型")
            return

        logger.info(f"准备并行启动 {len(auto_start_models)} 个自动启动模型: {auto_start_models}")

        def start_single_model(model_name):
            try:
                success, message = self.start_model(model_name)
                return model_name, success, message
            except Exception as e:
                logger.error(f"自动启动模型 {model_name} 时发生异常: {e}")
                return model_name, False, f"启动异常: {e}"

        # 使用线程池并行启动模型
        futures = []
        for model_name in auto_start_models:
            future = self.executor.submit(start_single_model, model_name)
            futures.append(future)

        # 收集结果
        started_models = []
        for future in concurrent.futures.as_completed(futures, timeout=120):
            try:
                model_name, success, message = future.result()
                if success:
                    logger.info(f"自动启动模型 {model_name} 成功")
                    started_models.append(model_name)
                else:
                    logger.error(f"自动启动模型 {model_name} 失败: {message}")
            except Exception as e:
                logger.error(f"处理模型启动结果时发生异常: {e}")

        logger.info(f"自动启动完成，成功: {len(started_models)}/{len(auto_start_models)}")
        if started_models:
            logger.info(f"成功启动的模型: {started_models}")

    def start_model(self, primary_name: str) -> Tuple[bool, str]:
        """
        优化的启动模型 - 支持并行启动，接受主名称

        Args:
            primary_name: 模型主名称

        Returns:
            (成功状态, 消息)
        """
        state = self.models_state[primary_name]
        model_lock = self.startup_locks[primary_name]

        # 快速检查，避免不必要的锁等待
        with state['lock']:
            if state['status'] == ModelStatus.ROUTING.value:
                return True, f"模型 '{primary_name}' 已在运行"
            elif state['status'] == ModelStatus.STARTING.value:
                # 模型正在启动，等待启动完成（最多等待30秒）
                return self._wait_for_model_startup(primary_name, state)

        # 使用模型专用锁而不是全局锁，允许并行启动
        if not model_lock.acquire(blocking=False):
            # 如果锁被占用，返回等待信息
            return False, f"模型 '{primary_name}' 正在启动中，请稍后再试"

        try:
            logger.info(f"开始启动模型 '{primary_name}'")

            # 双重检查：在获取锁后再次确认模型状态
            with state['lock']:
                if state['status'] == ModelStatus.ROUTING.value:
                    logger.info(f"模型 '{primary_name}' 已被其他线程启动")
                    return True, f"模型 {primary_name} 已成功启动"
                elif state['status'] == ModelStatus.STARTING.value:
                    logger.info(f"模型 '{primary_name}' 正在启动中，等待完成...")
                    # 释放模型锁并等待
                    model_lock.release()
                    return self._wait_for_model_startup(primary_name, state)

                # 确认由当前线程执行加载
                state['status'] = ModelStatus.STARTING.value
                # 使用新的日志管理器
                self.log_manager.add_log_entry(primary_name, f"--- {time.ctime()} --- 启动模型 '{primary_name}'", "info")

            try:
                return self._start_model_intelligent(primary_name)
            except Exception as e:
                with state['lock']:
                    state['status'] = ModelStatus.FAILED.value
                    state['failure_reason'] = str(e)
                logger.error(f"启动模型 {primary_name} 失败: {e}", exc_info=True)
                return False, f"启动模型 {primary_name} 失败: {e}"
        finally:
            model_lock.release()

    def _wait_for_model_startup(self, primary_name: str, state: Dict[str, Any]) -> Tuple[bool, str]:
        """
        等待模型启动完成

        Args:
            primary_name: 模型名称
            state: 模型状态

        Returns:
            (成功状态, 消息)
        """
        wait_start = time.time()
        max_wait_time = 30  # 减少等待时间到30秒

        while state['status'] == ModelStatus.STARTING.value:
            if time.time() - wait_start > max_wait_time:
                logger.error(f"等待模型 '{primary_name}' 启动超时")
                return False, f"等待模型 '{primary_name}' 启动超时"
            time.sleep(0.5)

        # 重新检查状态
        if state['status'] == ModelStatus.ROUTING.value:
            logger.info(f"模型 '{primary_name}' 启动完成")
            return True, f"模型 {primary_name} 已成功启动"
        else:
            logger.error(f"模型 '{primary_name}' 启动失败")
            return False, f"模型 {primary_name} 启动失败"

    def _start_model_intelligent(self, primary_name: str) -> Tuple[bool, str]:
        """
        智能启动模型 - 包含设备检查和资源管理

        Args:
            primary_name: 模型主名称

        Returns:
            (成功状态, 消息)
        """
        state = self.models_state[primary_name]

        # 获取自适应配置
        # 获取当前在线的设备
        online_devices = set()
        for device_name, device_plugin in self.plugin_manager.get_all_device_plugins().items():
            if device_plugin.is_online():
                online_devices.add(device_name)

        model_config = self.config_manager.get_adaptive_model_config(primary_name, online_devices)
        if not model_config:
            current_devices = [name for name, plugin in self.plugin_manager.get_all_device_plugins().items() if plugin.is_online()]
            error_msg = f"启动 '{primary_name}' 失败：没有适合当前设备状态 {current_devices} 的配置方案"
            state['status'] = ModelStatus.FAILED.value
            state['failure_reason'] = error_msg
            self.log_manager.add_log_entry(primary_name, error_msg, "error")
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
            self.log_manager.add_log_entry(primary_name, error_msg, "error")
            return False, error_msg

        # 启动模型
        logger.info(f"正在启动模型: {primary_name} (配置方案: {model_config.get('config_source', '默认')})")
        self.log_manager.add_log_entry(primary_name, f"使用配置方案: {model_config.get('config_source', '默认')}", "info")
        self.log_manager.add_log_entry(primary_name, f"启动脚本: {model_config['bat_path']}", "info")

        try:
            # 使用进程管理器启动模型进程
            project_root = os.path.dirname(os.path.abspath(self.config_manager.config_path))
            process_name = f"model_{primary_name}"

            # 定义输出回调函数，将进程输出转发到日志管理器
            def output_callback(stream_type: str, message: str):
                """进程输出回调函数"""
                # 直接记录原始输出，不添加任何级别标记
                self.log_manager.add_raw_output(primary_name, message)

            success, message, pid = self.process_manager.start_process(
                name=process_name,
                command=model_config['bat_path'],
                cwd=project_root,
                description=f"模型进程: {primary_name}",
                shell=True,
                creation_flags=subprocess.CREATE_NEW_PROCESS_GROUP,
                capture_output=True,
                output_callback=output_callback
            )

            if not success:
                error_msg = f"启动模型进程失败: {message}"
                state['status'] = ModelStatus.FAILED.value
                state['failure_reason'] = error_msg
                self.log_manager.add_log_entry(primary_name, error_msg, "error")
                return False, error_msg

            state.update({
                "process": None,
                "pid": pid,
                "log_thread": None,
                "stdout_thread": None,
                "stderr_thread": None
            })

            # 执行健康检查
            return self._perform_health_checks(primary_name, model_config)

        except Exception as e:
            logger.error(f"启动模型进程失败: {e}")
            state['status'] = ModelStatus.FAILED.value
            state['failure_reason'] = str(e)
            self.log_manager.add_log_entry(primary_name, f"启动模型进程失败: {e}", "error")
            return False, f"启动模型进程失败: {e}"

    def _check_and_free_resources(self, model_config: Dict[str, Any]) -> bool:
        """
        检查并释放设备资源

        Args:
            model_config: 模型配置

        Returns:
            是否有足够资源
        """
        required_memory = model_config.get("memory_mb", {})

        for attempt in range(3):  # 增加到3次尝试
            resource_ok = True
            deficit_devices = {}

            # 检查每个设备的内存
            for device_name, required_mb in required_memory.items():
                device_plugin = self.plugin_manager.get_device_plugin(device_name)
                if not device_plugin:
                    logger.warning(f"配置中的设备 '{device_name}' 未找到插件，跳过")
                    continue

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
        """
        停止空闲模型以释放资源

        Args:
            deficit_devices: 缺乏资源的设备列表
            model_to_start: 要启动的模型配置

        Returns:
            是否成功释放资源
        """
        idle_candidates = []

        for name, state in self.models_state.items():
            with state['lock']:
                if state['status'] == ModelStatus.ROUTING.value:
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
        """
        执行健康检查

        Args:
            primary_name: 模型主名称
            model_config: 模型配置

        Returns:
            (成功状态, 消息)
        """
        state = self.models_state[primary_name]
        port = model_config['port']
        timeout_seconds = 300
        start_time = time.time()

        # 健康检查
        with state['lock']:
            state['status'] = ModelStatus.HEALTH_CHECK.value

        logger.info(f"正在对模型 '{primary_name}' 进行健康检查")

        model_mode = model_config.get("mode", "Chat")
        interface_plugin = self.plugin_manager.get_interface_plugin(model_mode)

        if interface_plugin:
            # 使用插件的统一健康检查方法
            health_success, health_message = interface_plugin.health_check(primary_name, port, start_time, timeout_seconds)
            if health_success:
                logger.info(f"模型 '{primary_name}' 健康检查通过")
                with state['lock']:
                    state['status'] = ModelStatus.ROUTING.value
                    state['last_access'] = time.time()

                # 记录模型启动时间并启动定时监控
                self.runtime_monitor.record_model_start(primary_name)

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
        """
        停止模型 - 接受主名称

        Args:
            primary_name: 模型主名称

        Returns:
            (成功状态, 消息)
        """
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

        # 只有在模型被监控时才记录停止时间
        if self.runtime_monitor.is_model_monitored(primary_name):
            self.runtime_monitor.record_model_stop(primary_name)

        return True, f"模型 {primary_name} 已停止"

    def _mark_model_as_stopped(self, primary_name: str, acquire_lock: bool = True):
        """
        标记模型为已停止状态

        Args:
            primary_name: 模型主名称
            acquire_lock: 是否获取锁
        """
        state = self.models_state[primary_name]

        def update():
            state.update({
                "process": None,
                "pid": None,
                "status": ModelStatus.STOPPED.value,
                "last_access": None,
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

                    # 只有在模型被监控时才记录停止时间
                    if self.runtime_monitor.is_model_monitored(name):
                        self.runtime_monitor.record_model_stop(name)

        logger.info(f"所有模型均已卸载，共终止 {len(terminated_models)} 个模型进程")

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
                                   state['last_access'])

                        if is_idle and (now - state['last_access']) > alive_time_sec:
                            logger.info(f"模型 {name} 空闲超过 {alive_time_min} 分钟，正在自动关闭...")
                            self.stop_model(name)

            except Exception as e:
                logger.error(f"空闲检查线程出错: {e}", exc_info=True)

    def get_all_models_status(self) -> Dict[str, Dict[str, Any]]:
        """
        获取所有模型状态

        Returns:
            模型状态字典
        """
        status_copy = {}
        now = time.time()

        for primary_name, state in self.models_state.items():
            idle_seconds = (now - state['last_access']) if state.get('last_access') else -1
            config = self.config_manager.get_model_config(primary_name)

            # 获取当前在线的设备
            online_devices = set()
            for device_name, device_plugin in self.plugin_manager.get_all_device_plugins().items():
                if device_plugin.is_online():
                    online_devices.add(device_name)
            adaptive_config = self.config_manager.get_adaptive_model_config(primary_name, online_devices)

            status_copy[primary_name] = {
                "aliases": config.get("aliases", [primary_name]) if config else [primary_name],
                "status": state['status'],
                "pid": state['pid'],
                "idle_time_sec": f"{idle_seconds:.0f}" if idle_seconds != -1 else "N/A",
                "mode": config.get("mode", "Chat") if config else "Chat",
                "is_available": bool(adaptive_config),
                "current_bat_path": adaptive_config.get("bat_path", "") if adaptive_config else "无可用配置",
                "config_source": adaptive_config.get("config_source", "N/A") if adaptive_config else "N/A",
                "failure_reason": state.get('failure_reason')
            }

        return status_copy

    
    def get_model_logs(self, primary_name: str) -> List[Dict[str, Any]]:
        """
        获取模型的结构化日志 - 接受主名称

        Args:
            primary_name: 模型主名称

        Returns:
            结构化日志列表
        """
        try:
            return self.log_manager.get_logs(primary_name)
        except Exception as e:
            logger.error(f"获取模型日志失败: {e}")
            return [{"timestamp": time.time(), "level": "error", "message": f"获取日志失败: {str(e)}"}]

    def subscribe_to_model_logs(self, primary_name: str) -> queue.Queue:
        """
        订阅模型日志流

        Args:
            primary_name: 模型主名称

        Returns:
            订阅队列
        """
        return self.log_manager.subscribe_to_logs(primary_name)

    def unsubscribe_from_model_logs(self, primary_name: str, subscriber_queue: queue.Queue):
        """
        取消订阅模型日志流

        Args:
            primary_name: 模型主名称
            subscriber_queue: 订阅队列
        """
        self.log_manager.unsubscribe_from_logs(primary_name, subscriber_queue)

    def get_log_stats(self) -> Dict[str, Any]:
        """
        获取日志统计信息

        Returns:
            统计信息字典
        """
        return self.log_manager.get_log_stats()

    def get_model_list(self) -> Dict[str, Any]:
        """
        获取模型列表

        Returns:
            模型列表字典
        """
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

    
    
    def shutdown(self):
        """优化的关闭模型控制器 - 并行终止所有进程"""
        logger.info("正在快速关闭模型控制器...")
        self.is_running = False
        self.shutdown_event.set()

        # 取消所有未完成的启动任务
        for model_name, future in self.startup_futures.items():
            if not future.done():
                future.cancel()
                logger.info(f"取消模型 {model_name} 的启动任务")

        # 使用进程管理器并行终止所有模型进程
        logger.info("正在并行终止所有模型进程...")
        terminated_models = []

        # 构建停止任务列表
        stop_tasks = []
        for primary_name, state in self.models_state.items():
            with state['lock']:
                pid = state.get('pid')
                if pid:
                    process_name = f"model_{primary_name}"
                    stop_tasks.append((primary_name, process_name))

        # 并行停止进程
        def stop_model_task(primary_name, process_name):
            try:
                success, message = self.process_manager.stop_process(process_name, force=True, timeout=3)
                if success:
                    return primary_name, True, None
                else:
                    return primary_name, False, message
            except Exception as e:
                return primary_name, False, str(e)

        # 提交停止任务
        stop_futures = []
        for primary_name, process_name in stop_tasks:
            future = self.executor.submit(stop_model_task, primary_name, process_name)
            stop_futures.append(future)

        # 收集结果，设置超时
        timeout = len(stop_tasks) * 2 + 5  # 总超时时间
        try:
            for future in concurrent.futures.as_completed(stop_futures, timeout=timeout):
                try:
                    primary_name, success, message = future.result()
                    if success:
                        terminated_models.append(primary_name)

                        # 更新状态
                        state = self.models_state[primary_name]
                        with state['lock']:
                            state['pid'] = None
                            state['status'] = ModelStatus.STOPPED.value
                            state['process'] = None
                    else:
                        logger.warning(f"停止模型 {primary_name} 失败: {message}")
                except Exception as e:
                    logger.error(f"处理模型停止结果时发生异常: {e}")
        except concurrent.futures.TimeoutError:
            logger.error("模型停止超时")

        # 只记录正在运行的模型的停止时间
        for primary_name, state in self.models_state.items():
            if self.runtime_monitor.is_model_monitored(primary_name):
                self.runtime_monitor.record_model_stop(primary_name)

        # 关闭运行时间监控器
        self.runtime_monitor.shutdown()

        # 关闭日志管理器
        self.log_manager.shutdown()

        # 关闭线程池
        self.executor.shutdown(wait=True)

        logger.info(f"模型控制器关闭完成，已终止 {len(terminated_models)} 个模型进程")