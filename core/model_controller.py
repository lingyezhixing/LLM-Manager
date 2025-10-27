"""
重构后的模型控制器 - 基于组件化架构设计
解决并发启动问题的根本性重构版本

架构设计:
- ModelStateManager: 负责模型状态管理和状态转换
- ModelLifecycleManager: 负责模型启动/停止的具体执行
- ModelConcurrencyController: 负责并发控制和请求调度
- ModelController: 主控制器，提供统一的API接口
"""

import subprocess
import time
import threading
import logging
import os
import concurrent.futures
import queue
import asyncio
from typing import Dict, List, Tuple, Optional, Any, Callable
from enum import Enum
from dataclasses import dataclass
from contextlib import asynccontextmanager
from utils.logger import get_logger
from .plugin_system import PluginManager
from .config_manager import ConfigManager
from .process_manager import get_process_manager
from .data_manager import Monitor

logger = get_logger(__name__)


# ============================================================================
# 核心数据结构定义
# ============================================================================

class ModelStatus(Enum):
    """模型状态枚举"""
    STOPPED = "stopped"
    STARTING = "starting"
    INIT_SCRIPT = "init_script"
    HEALTH_CHECK = "health_check"
    ROUTING = "routing"
    FAILED = "failed"
    STOPPING = "stopping"


@dataclass
class ModelState:
    """模型状态数据"""
    status: ModelStatus
    pid: Optional[int] = None
    process: Optional[Any] = None
    start_time: Optional[float] = None
    last_access: Optional[float] = None
    failure_reason: Optional[str] = None
    current_config: Optional[Dict[str, Any]] = None


class Result:
    """统一的操作结果类"""
    def __init__(self, success: bool, value: Any = None, error: str = None):
        self.success = success
        self.value = value
        self.error = error

    def __bool__(self):
        return self.success

    def get_value_or_error(self) -> Tuple[Any, Optional[str]]:
        return self.value, self.error


# ============================================================================
# 日志管理器 (保持原有功能)
# ============================================================================

class LogManager:
    """日志管理器 - 负责模型控制台日志的收集、存储和流式推送"""

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

    def add_console_log(self, model_name: str, message: str):
        """添加模型控制台日志"""
        if model_name not in self.model_logs:
            self.register_model(model_name)

        log_entry = {
            "timestamp": time.time(),
            "message": message
        }

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


# ============================================================================
# 运行时监控器 (保持原有功能)
# ============================================================================

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


# ============================================================================
# 模型状态管理器 - 单一职责，负责状态管理
# ============================================================================

class ModelStateManager:
    """模型状态管理器 - 负责模型状态的存储、查询和原子转换"""

    def __init__(self):
        """初始化状态管理器"""
        self._states: Dict[str, ModelState] = {}
        self._lock = threading.RLock()  # 单一锁保护所有状态操作
        self._waiters: Dict[str, List[Callable]] = {}  # 状态等待者
        logger.info("模型状态管理器初始化完成")

    def register_model(self, model_name: str):
        """注册模型状态"""
        with self._lock:
            if model_name not in self._states:
                self._states[model_name] = ModelState(status=ModelStatus.STOPPED)
                self._waiters[model_name] = []
                logger.debug(f"注册模型状态: {model_name}")

    def get_state(self, model_name: str) -> Optional[ModelState]:
        """获取模型状态（返回副本）"""
        with self._lock:
            if model_name not in self._states:
                return None

            # 返回状态副本，避免外部修改
            state = self._states[model_name]
            return ModelState(
                status=state.status,
                pid=state.pid,
                process=state.process,
                start_time=state.start_time,
                last_access=state.last_access,
                failure_reason=state.failure_reason,
                current_config=state.current_config
            )

    def update_status(self, model_name: str, new_status: ModelStatus, **kwargs):
        """原子性更新模型状态"""
        with self._lock:
            if model_name not in self._states:
                self.register_model(model_name)

            state = self._states[model_name]
            old_status = state.status
            state.status = new_status

            # 更新其他属性
            for key, value in kwargs.items():
                if hasattr(state, key):
                    setattr(state, key, value)

            logger.debug(f"模型 {model_name} 状态转换: {old_status.value} -> {new_status.value}")

            # 通知等待者
            self._notify_waiters(model_name, new_status)

    def set_running(self, model_name: str, pid: int, config: Dict[str, Any]):
        """设置模型为运行状态"""
        self.update_status(
            model_name,
            ModelStatus.ROUTING,
            pid=pid,
            start_time=time.time(),
            last_access=time.time(),
            current_config=config,
            failure_reason=None
        )

    def set_stopped(self, model_name: str, failure_reason: Optional[str] = None):
        """设置模型为停止状态"""
        self.update_status(
            model_name,
            ModelStatus.STOPPED,
            pid=None,
            process=None,
            start_time=None,
            last_access=None,
            current_config=None,
            failure_reason=failure_reason
        )

    def set_failed(self, model_name: str, failure_reason: str):
        """设置模型为失败状态"""
        self.update_status(
            model_name,
            ModelStatus.FAILED,
            failure_reason=failure_reason
        )

    def update_last_access(self, model_name: str):
        """更新模型最后访问时间"""
        with self._lock:
            if model_name in self._states:
                self._states[model_name].last_access = time.time()

    def wait_for_status(self, model_name: str, target_statuses: List[ModelStatus],
                        timeout: float = 120.0) -> Result:
        """等待模型达到目标状态"""
        start_time = time.time()

        with self._lock:
            if model_name not in self._states:
                return Result(False, error=f"模型 {model_name} 未注册")

            current_status = self._states[model_name].status
            if current_status in target_statuses:
                return Result(True, value=current_status)

        # 注册等待者
        event = threading.Event()

        def status_checker(status: ModelStatus):
            if status in target_statuses:
                event.set()

        with self._lock:
            self._waiters[model_name].append(status_checker)

        try:
            while time.time() - start_time < timeout:
                remaining_time = timeout - (time.time() - start_time)
                if event.wait(timeout=min(1.0, remaining_time)):
                    # 获取最终状态
                    final_state = self.get_state(model_name)
                    return Result(True, value=final_state.status)

                # 检查模型是否失败或停止
                state = self.get_state(model_name)
                if state and state.status in [ModelStatus.FAILED, ModelStatus.STOPPED]:
                    if state.status not in target_statuses:
                        return Result(False, error=f"模型 {model_name} 状态变为 {state.status.value}: {state.failure_reason or '未知原因'}")

            return Result(False, error=f"等待模型 {model_name} 状态超时")

        finally:
            # 清理等待者
            with self._lock:
                if model_name in self._waiters and status_checker in self._waiters[model_name]:
                    self._waiters[model_name].remove(status_checker)

    def _notify_waiters(self, model_name: str, new_status: ModelStatus):
        """通知状态等待者"""
        if model_name in self._waiters:
            for waiter in self._waiters[model_name][:]:  # 创建副本避免迭代时修改
                try:
                    waiter(new_status)
                except Exception as e:
                    logger.error(f"通知状态等待者失败: {e}")
                    if waiter in self._waiters[model_name]:
                        self._waiters[model_name].remove(waiter)

    def get_all_states(self) -> Dict[str, ModelState]:
        """获取所有模型状态"""
        with self._lock:
            return {name: self.get_state(name) for name in self._states.keys()}

    def shutdown(self):
        """关闭状态管理器"""
        with self._lock:
            self._states.clear()
            self._waiters.clear()
        logger.info("模型状态管理器已关闭")


# ============================================================================
# 模型生命周期管理器 - 负责具体的启动/停止执行
# ============================================================================

class ModelLifecycleManager:
    """模型生命周期管理器 - 负责执行模型启动和停止的具体操作"""

    def __init__(self, config_manager: ConfigManager, state_manager: ModelStateManager,
                 log_manager: LogManager, runtime_monitor: ModelRuntimeMonitor):
        """
        初始化生命周期管理器

        Args:
            config_manager: 配置管理器
            state_manager: 状态管理器
            log_manager: 日志管理器
            runtime_monitor: 运行时监控器
        """
        self.config_manager = config_manager
        self.state_manager = state_manager
        self.log_manager = log_manager
        self.runtime_monitor = runtime_monitor
        self.process_manager = get_process_manager()
        self.plugin_manager: Optional[PluginManager] = None

        logger.info("模型生命周期管理器初始化完成")

    def set_plugin_manager(self, plugin_manager: PluginManager):
        """设置插件管理器"""
        self.plugin_manager = plugin_manager

    def start_model(self, model_name: str) -> Result:
        """启动模型的具体执行逻辑"""
        try:
            logger.info(f"开始启动模型: {model_name}")

            # 设置状态为启动中
            self.state_manager.update_status(model_name, ModelStatus.STARTING)

            # 获取自适应配置
            online_devices = self._get_online_devices()
            model_config = self.config_manager.get_adaptive_model_config(model_name, online_devices)

            if not model_config:
                error_msg = f"启动 '{model_name}' 失败：没有适合当前设备状态 {list(online_devices)} 的配置方案"
                self.state_manager.set_failed(model_name, error_msg)
                return Result(False, error=error_msg)

            # 保存当前配置到状态
            self.state_manager.update_status(model_name, ModelStatus.INIT_SCRIPT, current_config=model_config)

            # 检查并释放资源
            if not self._check_and_free_resources(model_config):
                error_msg = f"启动 '{model_name}' 失败：设备资源不足"
                self.state_manager.set_failed(model_name, error_msg)
                return Result(False, error=error_msg)

            # 启动模型进程
            start_result = self._start_model_process(model_name, model_config)
            if not start_result.success:
                self.state_manager.set_failed(model_name, start_result.error)
                return start_result

            pid = start_result.value

            # 执行健康检查
            self.state_manager.update_status(model_name, ModelStatus.HEALTH_CHECK)
            health_result = self._perform_health_check(model_name, model_config, pid)

            if not health_result.success:
                # 停止进程并设置失败状态
                self._stop_model_process(model_name)
                self.state_manager.set_failed(model_name, health_result.error)
                return health_result

            # 设置为运行状态
            self.state_manager.set_running(model_name, pid, model_config)

            # 启动运行时监控
            self.runtime_monitor.record_model_start(model_name)

            logger.info(f"模型 {model_name} 启动成功")
            return Result(True, value=f"模型 {model_name} 启动成功")

        except Exception as e:
            error_msg = f"启动模型 {model_name} 时发生异常: {str(e)}"
            logger.error(error_msg, exc_info=True)
            self.state_manager.set_failed(model_name, error_msg)
            return Result(False, error=error_msg)

    def stop_model(self, model_name: str) -> Result:
        """停止模型的具体执行逻辑"""
        try:
            logger.info(f"开始停止模型: {model_name}")

            state = self.state_manager.get_state(model_name)
            if not state:
                return Result(True, value=f"模型 {model_name} 未注册")

            if state.status == ModelStatus.STOPPED:
                return Result(True, value=f"模型 {model_name} 已停止")

            # 设置状态为停止中
            self.state_manager.update_status(model_name, ModelStatus.STOPPING)

            # 停止进程
            stop_result = self._stop_model_process(model_name)
            if not stop_result.success:
                logger.warning(f"停止模型进程失败: {stop_result.error}")

            # 停止运行时监控
            if self.runtime_monitor.is_model_monitored(model_name):
                self.runtime_monitor.record_model_stop(model_name)

            # 设置为停止状态
            self.state_manager.set_stopped(model_name)

            logger.info(f"模型 {model_name} 停止成功")
            return Result(True, value=f"模型 {model_name} 停止成功")

        except Exception as e:
            error_msg = f"停止模型 {model_name} 时发生异常: {str(e)}"
            logger.error(error_msg, exc_info=True)
            self.state_manager.set_failed(model_name, error_msg)
            return Result(False, error=error_msg)

    def _get_online_devices(self) -> set:
        """获取在线设备集合"""
        if not self.plugin_manager:
            return set()

        online_devices = set()
        for device_name, device_plugin in self.plugin_manager.get_all_device_plugins().items():
            if device_plugin.is_online():
                online_devices.add(device_name)
        return online_devices

    def _check_and_free_resources(self, model_config: Dict[str, Any]) -> bool:
        """检查并释放设备资源"""
        # 如果禁用了GPU监控，跳过所有资源检查
        if self.config_manager.is_gpu_monitoring_disabled():
            logger.info("GPU监控已禁用，跳过资源检查")
            return True

        required_memory = model_config.get("memory_mb", {})
        if not required_memory:
            logger.warning("模型配置中没有memory_mb信息，跳过资源检查")
            return True

        logger.debug(f"检查模型资源需求: {required_memory}")

        for attempt in range(3):  # 最多尝试3次
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
                logger.debug(f"设备 '{device_name}': 可用 {available_mb}MB, 需要 {required_mb}MB")

                if available_mb < required_mb:
                    deficit = required_mb - available_mb
                    deficit_devices[device_name] = deficit
                    resource_ok = False
                    logger.debug(f"设备 '{device_name}' 资源不足，缺少 {deficit}MB")

            # 只有当所有设备都满足要求时才返回True
            if resource_ok:
                logger.info("设备资源检查通过")
                return True

            logger.warning(f"设备资源不足，需要释放: {deficit_devices}")

            if attempt < 2:  # 前两次尝试都进行资源释放
                # 尝试停止空闲模型释放资源
                logger.info("尝试停止空闲模型以释放资源...")
                if self._stop_idle_models_for_resources(deficit_devices):
                    logger.info("资源释放完成，重新检查设备状态...")
                    time.sleep(3)  # 给系统一些时间来释放资源
                else:
                    logger.warning("无法释放足够的资源")
                    break
            else:
                logger.error("达到最大尝试次数，仍然资源不足")
                break

        return False

    def _stop_idle_models_for_resources(self, deficit_devices: Dict[str, int]) -> bool:
        """停止空闲模型以释放资源"""
        # 如果禁用了GPU监控，不进行资源释放
        if self.config_manager.is_gpu_monitoring_disabled():
            logger.info("GPU监控已禁用，不进行资源释放")
            return False

        target_devices = set(deficit_devices.keys())
        logger.debug(f"需要释放资源的设备: {target_devices}")

        idle_candidates = []
        all_states = self.state_manager.get_all_states()

        for name, state in all_states.items():
            if state.status == ModelStatus.ROUTING:
                # 检查模型是否在目标设备上运行
                model_config = state.current_config
                if not model_config:
                    logger.debug(f"模型 {name} 没有当前配置信息，跳过")
                    continue

                # 优先使用required_devices信息
                if "required_devices" in model_config:
                    model_devices = set(model_config["required_devices"])
                else:
                    # 备选方案：从模型配置中推断设备信息
                    model_devices = set()
                    for config_key, config_data in model_config.items():
                        if config_key not in ["bat_path", "memory_mb", "config_source", "required_devices"]:
                            # 这个config_key就是设备配置名
                            model_devices.add(config_key)

                # 检查设备是否有交集
                if model_devices.intersection(target_devices):
                    logger.debug(f"模型 {name} 运行在设备 {model_devices} 上，与目标设备 {target_devices} 有交集")
                    idle_candidates.append((name, state.last_access or 0, model_devices))
                else:
                    logger.debug(f"模型 {name} 运行在设备 {model_devices} 上，与目标设备 {target_devices} 无交集，跳过")

        # 按最后访问时间排序（最久未访问的优先停止）
        sorted_idle_models = sorted(idle_candidates, key=lambda x: x[1])

        logger.info(f"找到 {len(sorted_idle_models)} 个运行在目标设备上的空闲模型可供停止")

        for model_name, _, model_devices in sorted_idle_models:
            logger.info(f"为释放资源，正在停止空闲模型: {model_name} (运行设备: {model_devices})")
            success = self.stop_model(model_name)

            if success:
                logger.info(f"模型 {model_name} 已停止，等待系统释放资源...")
                time.sleep(2)  # 给系统一些时间来释放资源
                return True  # 返回True让主循环重新检查设备状态
            else:
                logger.warning(f"停止模型 {model_name} 失败")

        logger.warning("无法释放足够的资源：没有可停止的相关空闲模型")
        return False

    def _start_model_process(self, model_name: str, model_config: Dict[str, Any]) -> Result:
        """启动模型进程"""
        try:
            project_root = os.path.dirname(os.path.abspath(self.config_manager.config_path))
            process_name = f"model_{model_name}"

            # 定义输出回调函数，将进程输出转发到日志管理器
            def output_callback(stream_type: str, message: str):
                """进程输出回调函数"""
                self.log_manager.add_console_log(model_name, message)

            success, message, pid = self.process_manager.start_process(
                name=process_name,
                command=model_config['bat_path'],
                cwd=project_root,
                description=f"模型进程: {model_name}",
                shell=True,
                creation_flags=subprocess.CREATE_NEW_PROCESS_GROUP,
                capture_output=True,
                output_callback=output_callback
            )

            if success:
                logger.info(f"模型进程启动成功: {model_name} (PID: {pid})")
                return Result(True, value=pid)
            else:
                error_msg = f"启动模型进程失败: {message}"
                logger.error(error_msg)
                return Result(False, error=error_msg)

        except Exception as e:
            error_msg = f"启动模型进程异常: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return Result(False, error=error_msg)

    def _stop_model_process(self, model_name: str) -> Result:
        """停止模型进程"""
        try:
            process_name = f"model_{model_name}"
            success, message = self.process_manager.stop_process(process_name, force=True)

            if success:
                logger.info(f"模型进程停止成功: {model_name}")
                return Result(True, value=f"模型进程已停止")
            else:
                error_msg = f"停止模型进程失败: {message}"
                logger.error(error_msg)
                return Result(False, error=error_msg)

        except Exception as e:
            error_msg = f"停止模型进程异常: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return Result(False, error=error_msg)

    def _perform_health_check(self, model_name: str, model_config: Dict[str, Any], pid: int) -> Result:
        """执行健康检查"""
        try:
            port = model_config['port']
            timeout_seconds = 300
            start_time = time.time()

            logger.info(f"正在对模型 '{model_name}' 进行健康检查")

            model_mode = model_config.get("mode", "Chat")
            interface_plugin = self.plugin_manager.get_interface_plugin(model_mode)

            if not interface_plugin:
                error_msg = f"未找到模式 '{model_mode}' 的接口插件，无法进行健康检查"
                logger.error(error_msg)
                return Result(False, error=error_msg)

            # 使用插件的统一健康检查方法
            health_success, health_message = interface_plugin.health_check(
                model_name, port, start_time, timeout_seconds
            )

            if health_success:
                logger.info(f"模型 '{model_name}' 健康检查通过")
                return Result(True, value=health_message)
            else:
                logger.error(f"模型 '{model_name}' 健康检查失败: {health_message}")
                return Result(False, error=health_message)

        except Exception as e:
            error_msg = f"健康检查异常: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return Result(False, error=error_msg)

    def shutdown(self):
        """关闭生命周期管理器"""
        logger.info("模型生命周期管理器已关闭")


# ============================================================================
# 并发控制器 - 负责并发请求的调度和控制
# ============================================================================

class ModelConcurrencyController:
    """模型并发控制器 - 负责并发请求的调度，防止重复启动"""

    def __init__(self, state_manager: ModelStateManager, lifecycle_manager: ModelLifecycleManager):
        """
        初始化并发控制器

        Args:
            state_manager: 状态管理器
            lifecycle_manager: 生命周期管理器
        """
        self.state_manager = state_manager
        self.lifecycle_manager = lifecycle_manager
        self._operation_locks: Dict[str, threading.Lock] = {}  # 每个模型的操作锁
        self._global_lock = threading.Lock()

        logger.info("模型并发控制器初始化完成")

    def _get_model_lock(self, model_name: str) -> threading.Lock:
        """获取模型的专用锁"""
        with self._global_lock:
            if model_name not in self._operation_locks:
                self._operation_locks[model_name] = threading.Lock()
            return self._operation_locks[model_name]

    def start_model(self, model_name: str) -> Result:
        """启动模型 - 单一锁机制，避免死锁"""
        model_lock = self._get_model_lock(model_name)

        # 使用单一锁保护整个启动过程
        with model_lock:
            # 检查当前状态
            current_state = self.state_manager.get_state(model_name)

            if current_state and current_state.status == ModelStatus.ROUTING:
                return Result(True, value=f"模型 '{model_name}' 已在运行")

            if current_state and current_state.status == ModelStatus.STARTING:
                # 等待启动完成
                logger.info(f"模型 '{model_name}' 正在启动中，等待完成...")
                wait_result = self.state_manager.wait_for_status(
                    model_name,
                    [ModelStatus.ROUTING, ModelStatus.FAILED, ModelStatus.STOPPED],
                    timeout=120.0
                )

                if wait_result.success:
                    final_status = wait_result.value
                    if final_status == ModelStatus.ROUTING:
                        return Result(True, value=f"模型 '{model_name}' 启动成功")
                    else:
                        error_state = self.state_manager.get_state(model_name)
                        error_msg = error_state.failure_reason if error_state else "启动失败"
                        return Result(False, error=f"模型 '{model_name}' 启动失败: {error_msg}")
                else:
                    return wait_result

            # 执行启动
            return self.lifecycle_manager.start_model(model_name)

    def stop_model(self, model_name: str) -> Result:
        """停止模型"""
        model_lock = self._get_model_lock(model_name)

        with model_lock:
            # 检查当前状态
            current_state = self.state_manager.get_state(model_name)

            if not current_state or current_state.status == ModelStatus.STOPPED:
                return Result(True, value=f"模型 '{model_name}' 已停止")

            if current_state.status == ModelStatus.STARTING:
                # 尝试中断启动过程
                logger.info(f"模型 '{model_name}' 正在启动中，尝试中断...")

                # 等待启动完成或失败
                wait_result = self.state_manager.wait_for_status(
                    model_name,
                    [ModelStatus.ROUTING, ModelStatus.FAILED, ModelStatus.STOPPED],
                    timeout=30.0
                )

                if wait_result.success:
                    # 如果启动成功了，再执行停止
                    return self.lifecycle_manager.stop_model(model_name)
                else:
                    # 启动失败或超时，直接设置停止状态
                    self.state_manager.set_stopped(model_name, "被用户请求停止")
                    return Result(True, value=f"已中断模型 '{model_name}' 的启动过程")

            # 执行停止
            return self.lifecycle_manager.stop_model(model_name)

    def update_last_access(self, model_name: str):
        """更新模型最后访问时间"""
        self.state_manager.update_last_access(model_name)

    def get_all_models_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有模型状态"""
        all_states = self.state_manager.get_all_states()
        result = {}

        for model_name, state in all_states.items():
            config = self.lifecycle_manager.config_manager.get_model_config(model_name)

            # 获取当前在线的设备
            online_devices = set()
            if self.lifecycle_manager.plugin_manager:
                for device_name, device_plugin in self.lifecycle_manager.plugin_manager.get_all_device_plugins().items():
                    if device_plugin.is_online():
                        online_devices.add(device_name)

            adaptive_config = self.lifecycle_manager.config_manager.get_adaptive_model_config(model_name, online_devices)

            idle_seconds = -1
            if state.last_access:
                idle_seconds = int(time.time() - state.last_access)

            result[model_name] = {
                "aliases": config.get("aliases", [model_name]) if config else [model_name],
                "status": state.status.value,
                "pid": state.pid,
                "idle_time_sec": str(idle_seconds) if idle_seconds != -1 else "N/A",
                "mode": config.get("mode", "Chat") if config else "Chat",
                "is_available": bool(adaptive_config),
                "current_bat_path": adaptive_config.get("bat_path", "") if adaptive_config else "无可用配置",
                "config_source": adaptive_config.get("config_source", "N/A") if adaptive_config else "N/A",
                "failure_reason": state.failure_reason
            }

        return result

    def shutdown(self):
        """关闭并发控制器"""
        with self._global_lock:
            self._operation_locks.clear()
        logger.info("模型并发控制器已关闭")


# ============================================================================
# 主控制器 - 提供统一的API接口
# ============================================================================

class ModelController:
    """重构后的模型控制器 - 提供统一的API接口，内部使用组件化架构"""

    def __init__(self, config_manager: ConfigManager):
        """
        初始化模型控制器

        Args:
            config_manager: 配置管理器实例
        """
        self.config_manager = config_manager

        # 初始化组件
        self.state_manager = ModelStateManager()
        self.log_manager = LogManager()
        self.runtime_monitor = ModelRuntimeMonitor()
        self.lifecycle_manager = ModelLifecycleManager(
            config_manager, self.state_manager, self.log_manager, self.runtime_monitor
        )
        self.concurrency_controller = ModelConcurrencyController(
            self.state_manager, self.lifecycle_manager
        )

        # 线程池
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)

        # 控制标志
        self.is_running = True
        self.shutdown_event = threading.Event()

        # 空闲检查线程
        self.idle_check_thread = threading.Thread(target=self._idle_check_loop, daemon=True)
        self.idle_check_thread.start()

        # 初始化模型状态
        self._initialize_model_states()

        logger.info("重构后的模型控制器初始化完成")

    def _initialize_model_states(self):
        """初始化模型状态"""
        model_names = self.config_manager.get_model_names()
        for model_name in model_names:
            self.state_manager.register_model(model_name)
            self.log_manager.register_model(model_name)

    def load_plugins(self):
        """加载设备插件和接口插件"""
        # 从配置管理器获取插件目录
        device_dir = self.config_manager.get_device_plugin_dir()
        interface_dir = self.config_manager.get_interface_plugin_dir()

        # 创建插件管理器
        plugin_manager = PluginManager(device_dir, interface_dir)
        self.lifecycle_manager.set_plugin_manager(plugin_manager)

        # 加载所有插件
        try:
            result = plugin_manager.load_all_plugins(model_manager=self)
            logger.info(f"设备插件自动加载完成: {list(plugin_manager.get_all_device_plugins().keys())}")
            logger.info(f"接口插件自动加载完成: {list(plugin_manager.get_all_interface_plugins().keys())}")

            # 检查是否有设备在线
            online_devices = [name for name, plugin in plugin_manager.get_all_device_plugins().items() if plugin.is_online()]
            if online_devices:
                logger.info(f"在线设备: {online_devices}")
            else:
                logger.warning("未检测到在线设备")

        except Exception as e:
            logger.error(f"自动加载插件失败: {e}")

    # ========================================================================
    # 公共API接口 - 保持与原版完全一致
    # ========================================================================

    def start_model(self, primary_name: str) -> Tuple[bool, str]:
        """
        启动模型 - 保持原有API不变

        Args:
            primary_name: 模型主名称

        Returns:
            (成功状态, 消息)
        """
        result = self.concurrency_controller.start_model(primary_name)
        if result.success:
            return True, result.value
        else:
            return False, result.error

    def stop_model(self, primary_name: str) -> Tuple[bool, str]:
        """
        停止模型 - 保持原有API不变

        Args:
            primary_name: 模型主名称

        Returns:
            (成功状态, 消息)
        """
        result = self.concurrency_controller.stop_model(primary_name)
        if result.success:
            return True, result.value
        else:
            return False, result.error

    def get_all_models_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有模型状态 - 保持原有API不变"""
        return self.concurrency_controller.get_all_models_status()

    def get_model_logs(self, primary_name: str) -> List[Dict[str, Any]]:
        """获取模型日志 - 保持原有API不变"""
        return self.log_manager.get_logs(primary_name)

    def subscribe_to_model_logs(self, primary_name: str) -> queue.Queue:
        """订阅模型日志流 - 保持原有API不变"""
        return self.log_manager.subscribe_to_logs(primary_name)

    def unsubscribe_from_model_logs(self, primary_name: str, subscriber_queue: queue.Queue):
        """取消订阅模型日志流 - 保持原有API不变"""
        self.log_manager.unsubscribe_from_logs(primary_name, subscriber_queue)

    def get_log_stats(self) -> Dict[str, Any]:
        """获取日志统计信息 - 保持原有API不变"""
        return self.log_manager.get_log_stats()

    def get_model_list(self) -> Dict[str, Any]:
        """获取模型列表 - 保持原有API不变"""
        data = []
        model_names = self.config_manager.get_model_names()

        for primary_name in model_names:
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

    def start_auto_start_models(self):
        """启动自动启动模型 - 保持原有API不变"""
        logger.info("检查需要自动启动的模型...")

        # 检查设备在线状态
        if not self.lifecycle_manager.plugin_manager:
            logger.warning("插件管理器未初始化，跳过自动启动模型")
            return

        online_devices = [name for name, plugin in self.lifecycle_manager.plugin_manager.get_all_device_plugins().items() if plugin.is_online()]
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

    def unload_all_models(self):
        """卸载所有运行中的模型 - 保持原有API不变"""
        logger.info("正在卸载所有运行中的模型...")
        all_states = self.state_manager.get_all_states()

        stopped_count = 0
        for model_name, state in all_states.items():
            if state.status not in [ModelStatus.STOPPED, ModelStatus.FAILED]:
                success, _ = self.stop_model(model_name)
                if success:
                    stopped_count += 1

        logger.info(f"所有模型均已卸载，共终止 {stopped_count} 个模型进程")
        return stopped_count

    def _idle_check_loop(self):
        """空闲检查循环 - 保持原有逻辑"""
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

                all_states = self.state_manager.get_all_states()
                for model_name, state in all_states.items():
                    is_idle = (state.status == ModelStatus.ROUTING and state.last_access)

                    if is_idle and (now - state.last_access) > alive_time_sec:
                        logger.info(f"模型 {model_name} 空闲超过 {alive_time_min} 分钟，正在自动关闭...")
                        self.stop_model(model_name)

            except Exception as e:
                logger.error(f"空闲检查线程出错: {e}", exc_info=True)

    def shutdown(self):
        """关闭模型控制器 - 保持原有API不变"""
        logger.info("正在关闭模型控制器...")
        self.is_running = False
        self.shutdown_event.set()

        # 卸载所有模型
        self.unload_all_models()

        # 关闭各个组件
        self.runtime_monitor.shutdown()
        self.log_manager.shutdown()
        self.lifecycle_manager.shutdown()
        self.concurrency_controller.shutdown()
        self.state_manager.shutdown()

        # 关闭线程池
        self.executor.shutdown(wait=True)

        logger.info("模型控制器已关闭")


# ============================================================================
# 为了向后兼容，保留原有的属性访问方式
# ============================================================================

# 为 ModelController 添加属性访问器，使其与原代码兼容
def _add_backward_compatibility():
    """添加向后兼容性"""
    original_init = ModelController.__init__

    def compat_init(self, config_manager):
        original_init(self, config_manager)

        # 添加兼容性属性
        self.models_state = {}
        self.plugin_manager = None

        # 定期更新 models_state 以保持兼容性
        def update_compat_state():
            while self.is_running:
                try:
                    self.models_state = self._convert_states_to_legacy_format()
                    time.sleep(1)
                except Exception as e:
                    logger.error(f"更新兼容状态失败: {e}")
                    time.sleep(5)

        compat_thread = threading.Thread(target=update_compat_state, daemon=True)
        compat_thread.start()

        # 延迟加载插件管理器
        def load_plugins_with_compat():
            self.load_plugins()
            if self.lifecycle_manager.plugin_manager:
                self.plugin_manager = self.lifecycle_manager.plugin_manager

        # 在后台线程中加载插件
        plugin_thread = threading.Thread(target=load_plugins_with_compat, daemon=True)
        plugin_thread.start()

    def _convert_states_to_legacy_format(self):
        """将新状态格式转换为旧格式以保持兼容性"""
        all_states = self.state_manager.get_all_states()
        legacy_states = {}

        for model_name, state in all_states.items():
            legacy_states[model_name] = {
                "process": None,
                "status": state.status.value,
                "last_access": state.last_access,
                "pid": state.pid,
                "lock": threading.Lock(),  # 创建虚拟锁以保持兼容
                "log_thread": None,
                "current_config": state.current_config,
                "failure_reason": state.failure_reason
            }

        return legacy_states

    ModelController.__init__ = compat_init
    ModelController._convert_states_to_legacy_format = _convert_states_to_legacy_format

# 应用向后兼容性
_add_backward_compatibility()