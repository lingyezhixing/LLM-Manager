"""
模型控制器 (Model Controller)
===========================

负责模型的生命周期管理，包括：
1. 启动、停止模型进程
2. 资源智能管理（自动释放空闲模型以腾出显存）
3. 并行启动与死锁防护
4. 跨平台路径适配与日志流管理

优化点：
- 支持并行启动模型
- 智能资源管理与循环释放策略
- 修复自动关闭时的死锁问题 (使用 RLock)
- Linux/Windows 路径与执行逻辑兼容
"""

import os
import time
import queue
import threading
import subprocess
import concurrent.futures
from enum import Enum
from typing import Dict, List, Tuple, Optional, Any

from utils.logger import get_logger
from .plugin_system import PluginManager
from .config_manager import ConfigManager
from .process_manager import get_process_manager
from .data_manager import Monitor

logger = get_logger(__name__)


class ModelStatus(Enum):
    """模型状态枚举"""
    STOPPED = "stopped"
    STARTING = "starting"
    INIT_SCRIPT = "init_script"
    HEALTH_CHECK = "health_check"
    ROUTING = "routing"
    FAILED = "failed"


class LogManager:
    """
    日志管理器
    
    负责模型控制台日志的收集、存储、流式推送以及定期清理。
    """

    def __init__(self):
        self.model_logs: Dict[str, List[Dict[str, Any]]] = {}
        self.log_subscribers: Dict[str, List[queue.Queue]] = {}
        self.log_locks: Dict[str, threading.Lock] = {}
        self.global_lock = threading.Lock()
        self._running = True
        logger.info("日志管理器初始化完成")

    def register_model(self, model_name: str):
        """注册模型的日志存储空间"""
        with self.global_lock:
            if model_name not in self.model_logs:
                self.model_logs[model_name] = []
                self.log_subscribers[model_name] = []
                self.log_locks[model_name] = threading.Lock()
                logger.debug(f"已注册模型日志管理: {model_name}")

    def unregister_model(self, model_name: str):
        """注销模型的日志管理并通知订阅者"""
        with self.global_lock:
            if model_name in self.model_logs:
                # 发送结束信号给所有订阅者
                for subscriber_queue in self.log_subscribers[model_name]:
                    try:
                        subscriber_queue.put(None)
                    except Exception:
                        pass

                del self.model_logs[model_name]
                del self.log_subscribers[model_name]
                del self.log_locks[model_name]
                logger.debug(f"已注销模型日志管理: {model_name}")

    def add_console_log(self, model_name: str, message: str):
        """添加一条模型控制台日志并推送到订阅流"""
        if model_name not in self.model_logs:
            self.register_model(model_name)

        log_entry = {
            "timestamp": time.time(),
            "message": message
        }

        with self.log_locks[model_name]:
            self.model_logs[model_name].append(log_entry)

        self._notify_subscribers(model_name, log_entry)

    def get_logs(self, model_name: str, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """获取指定模型的历史日志"""
        if model_name not in self.model_logs:
            return []

        with self.log_locks[model_name]:
            logs = self.model_logs[model_name].copy()

        if limit is not None:
            logs = logs[-limit:]
        return logs

    def get_all_logs(self) -> Dict[str, List[Dict[str, Any]]]:
        """获取所有模型的日志"""
        with self.global_lock:
            result = {}
            for model_name in self.model_logs:
                result[model_name] = self.get_logs(model_name)
            return result

    def clear_logs(self, model_name: str):
        """清空指定模型的日志"""
        if model_name in self.model_logs:
            with self.log_locks[model_name]:
                self.model_logs[model_name].clear()
            logger.debug(f"已清空模型日志: {model_name}")

    def cleanup_old_logs(self, model_name: str, keep_minutes: int) -> int:
        """
        清理过期的日志
        
        Args:
            model_name: 模型名称
            keep_minutes: 保留最近多少分钟的日志
            
        Returns:
            删除的条目数量
        """
        if model_name not in self.model_logs:
            return 0

        current_time = time.time()
        cutoff_time = current_time - (keep_minutes * 60)

        with self.log_locks[model_name]:
            original_count = len(self.model_logs[model_name])
            self.model_logs[model_name] = [
                log for log in self.model_logs[model_name]
                if log['timestamp'] > cutoff_time
            ]
            removed_count = original_count - len(self.model_logs[model_name])

        if removed_count > 0:
            logger.debug(f"模型 '{model_name}' 日志清理: 删除 {keep_minutes} 分钟前的日志 {removed_count} 条")
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
        """取消订阅"""
        if model_name in self.log_subscribers:
            with self.global_lock:
                if subscriber_queue in self.log_subscribers[model_name]:
                    self.log_subscribers[model_name].remove(subscriber_queue)
                    logger.debug(f"订阅者离开模型日志流: {model_name}")

    def _notify_subscribers(self, model_name: str, log_entry: Dict[str, Any]):
        """内部方法：向所有订阅者广播新日志"""
        if model_name not in self.log_subscribers:
            return

        subscribers_copy = self.log_subscribers[model_name].copy()
        for subscriber_queue in subscribers_copy:
            try:
                subscriber_queue.put_nowait(log_entry)
            except queue.Full:
                self._remove_subscriber(model_name, subscriber_queue, reason="队列已满")
            except Exception:
                self._remove_subscriber(model_name, subscriber_queue, reason="发生异常")

    def _remove_subscriber(self, model_name: str, subscriber_queue: queue.Queue, reason: str):
        """内部方法：移除无效订阅者"""
        with self.global_lock:
            if subscriber_queue in self.log_subscribers.get(model_name, []):
                self.log_subscribers[model_name].remove(subscriber_queue)
                logger.debug(f"移除订阅者 ({reason}): {model_name}")

    def get_log_stats(self) -> Dict[str, Any]:
        """获取日志系统的统计信息"""
        with self.global_lock:
            stats = {
                "total_models": len(self.model_logs),
                "total_log_entries": sum(len(logs) for logs in self.model_logs.values()),
                "total_subscribers": sum(len(subs) for subs in self.log_subscribers.values()),
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
        with self.global_lock:
            for _, subscribers in self.log_subscribers.items():
                for q in subscribers:
                    try:
                        q.put(None)
                    except Exception:
                        pass
        logger.info("日志管理器已关闭")


class ModelRuntimeMonitor:
    """
    模型运行时间监控器
    
    负责记录模型的启动时间、运行时长，并定期写入数据库。
    """

    def __init__(self, db_path: Optional[str] = None):
        self.monitor = Monitor(db_path)
        self.active_models: Dict[str, threading.Timer] = {}
        self.lock = threading.Lock()
        logger.info("模型运行时间监控器初始化完成")

    def record_model_start(self, model_name: str) -> bool:
        """记录模型启动时间并开启定时更新任务"""
        start_time = time.time()
        try:
            self.monitor.add_model_runtime_start(model_name, start_time)
            logger.info(f"已记录模型 '{model_name}' 启动时间: {start_time}")
        except Exception as e:
            logger.error(f"记录模型 '{model_name}' 启动时间失败: {e}")
            return False

        with self.lock:
            if model_name in self.active_models:
                self.active_models[model_name].cancel()

            # 每10秒更新一次运行时长
            timer = threading.Timer(10.0, self._update_runtime_periodically, args=[model_name])
            timer.daemon = True
            timer.start()
            self.active_models[model_name] = timer

        logger.info(f"已启动模型 '{model_name}' 的运行时间定时更新器")
        return True

    def _update_runtime_periodically(self, model_name: str):
        """定期更新数据库中的结束时间"""
        with self.lock:
            if model_name in self.active_models:
                end_time = time.time()
                try:
                    self.monitor.update_model_runtime_end(model_name, end_time)
                    logger.debug(f"已更新模型 '{model_name}' 运行时间: {end_time}")
                except Exception as e:
                    logger.error(f"更新模型 '{model_name}' 运行时间失败: {e}")

                # 重新设置定时器
                timer = threading.Timer(10.0, self._update_runtime_periodically, args=[model_name])
                timer.daemon = True
                timer.start()
                self.active_models[model_name] = timer

    def record_model_stop(self, model_name: str) -> bool:
        """记录模型停止时间并关闭定时器"""
        end_time = time.time()
        with self.lock:
            if model_name in self.active_models:
                self.active_models[model_name].cancel()
                del self.active_models[model_name]

        try:
            self.monitor.update_model_runtime_end(model_name, end_time)
            logger.info(f"已记录模型 '{model_name}' 停止时间: {end_time}")
            return True
        except Exception as e:
            logger.error(f"记录模型 '{model_name}' 停止时间失败: {e}")
            return False

    def is_model_monitored(self, model_name: str) -> bool:
        with self.lock:
            return model_name in self.active_models

    def get_active_models(self) -> List[str]:
        with self.lock:
            return list(self.active_models.keys())

    def shutdown(self):
        with self.lock:
            for name, timer in self.active_models.items():
                timer.cancel()
                logger.info(f"已停止模型 '{name}' 的运行时间监控")
            self.active_models.clear()

        try:
            self.monitor.close()
            logger.info("模型运行时间监控器已关闭")
        except Exception as e:
            logger.error(f"关闭模型运行时间监控器失败: {e}")

    def __del__(self):
        try:
            self.shutdown()
        except Exception:
            pass


class ModelController:
    """
    优化的模型控制器
    
    核心功能：
    - 管理模型生命周期（启动/停止/状态查询）
    - 并行启动支持
    - 智能资源管理与释放
    - 死锁修复与 Linux 适配
    """

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.models_state: Dict[str, Dict[str, Any]] = {}
        self.is_running = True
        self.plugin_manager: Optional[PluginManager] = None
        self.process_manager = get_process_manager()
        self.runtime_monitor = ModelRuntimeMonitor()
        self.log_manager = LogManager()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
        self.startup_futures: Dict[str, concurrent.futures.Future] = {}
        self.startup_locks: Dict[str, threading.Lock] = {}
        self.shutdown_event = threading.Event()
        self.api_router: Optional[Any] = None

        # 启动空闲检查线程
        self.idle_check_thread = threading.Thread(target=self.idle_check_loop, daemon=True)
        self.idle_check_thread.start()

        # 初始化模型状态结构
        self._init_models_state()
        
        # 加载插件
        self.load_plugins()

    def _init_models_state(self):
        """初始化内部模型状态字典"""
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
                    # 【核心修复】使用 RLock 替代 Lock，允许同一线程（如 idle_check 调用 stop_model 时）重入锁
                    # 这直接解决了自动关闭时的死锁问题
                    "lock": threading.RLock(),
                    "log_thread": None,
                    "current_config": None,
                    "failure_reason": None
                }
                # 为每个模型创建专用的启动锁
                self.startup_locks[primary_name] = threading.Lock()
                self.log_manager.register_model(primary_name)

        self.models_state = new_states

    def set_api_router(self, api_router: Any):
        """注入 API Router 实例，用于查询待处理请求数"""
        self.api_router = api_router
        logger.info("API Router 实例已成功注入到 Model Controller")

    def load_plugins(self):
        """加载设备和接口插件，并更新初始设备状态"""
        device_dir = self.config_manager.get_device_plugin_dir()
        interface_dir = self.config_manager.get_interface_plugin_dir()
        self.plugin_manager = PluginManager(device_dir, interface_dir)

        try:
            self.plugin_manager.load_all_plugins(model_manager=self)
            logger.info(f"设备插件已加载: {list(self.plugin_manager.get_all_device_plugins().keys())}")
            logger.info(f"接口插件已加载: {list(self.plugin_manager.get_all_interface_plugins().keys())}")
            
            logger.info("正在初始化设备状态缓存...")
            self.plugin_manager.update_device_status()

            online_devices = self.plugin_manager.get_cached_online_devices()
            if online_devices:
                logger.info(f"当前在线设备: {list(online_devices)}")
            else:
                logger.warning("未检测到在线设备")

        except Exception as e:
            logger.error(f"自动加载插件失败: {e}")

    def _check_if_cancelled(self, primary_name: str) -> bool:
        """
        检查启动流程是否被取消

        Args:
            primary_name: 模型名称

        Returns:
            True 表示被取消，False 表示可以继续
        """
        state = self.models_state[primary_name]

        with state['lock']:
            cancelled = state['status'] == ModelStatus.STOPPED.value
            if cancelled:
                logger.info(f"检测到停止信号，取消启动: {primary_name}")
                # 确保状态一致
                state['failure_reason'] = "启动被用户中断"

        return cancelled

    def start_auto_start_models(self):
        """并行启动所有标记为自动启动的模型"""
        logger.info("检查自动启动模型配置...")

        online_devices = self.plugin_manager.get_cached_online_devices()
        
        # 如果未禁用监控且无设备在线，跳过自动启动
        if not online_devices and not self.config_manager.is_gpu_monitoring_disabled():
            logger.warning("没有在线设备，跳过自动启动")
            return

        auto_start_models = [
            name for name in self.config_manager.get_model_names()
            if self.config_manager.is_auto_start(name)
        ]

        if not auto_start_models:
            logger.info("没有配置为自动启动的模型")
            return

        logger.info(f"准备并行启动 {len(auto_start_models)} 个模型: {auto_start_models}")

        def start_single_model(model_name):
            try:
                success, msg = self.start_model(model_name)
                return model_name, success, msg
            except Exception as ex:
                logger.error(f"自动启动模型 {model_name} 异常: {ex}")
                return model_name, False, f"异常: {ex}"

        futures = []
        for model_name in auto_start_models:
            futures.append(self.executor.submit(start_single_model, model_name))

        started_models = []
        for future in concurrent.futures.as_completed(futures, timeout=120):
            try:
                name, success, msg = future.result()
                if success:
                    logger.info(f"自动启动模型 {name} 成功")
                    started_models.append(name)
                else:
                    logger.error(f"自动启动模型 {name} 失败: {msg}")
            except Exception as e:
                logger.error(f"处理启动结果时异常: {e}")

        logger.info(f"自动启动流程结束，成功: {len(started_models)}/{len(auto_start_models)}")

    def start_model(self, primary_name: str) -> Tuple[bool, str]:
        """
        启动模型（修复逻辑：移除错误的初始状态检查）
        """
        state = self.models_state[primary_name]
        model_lock = self.startup_locks[primary_name]

        # 快速状态检查（不加锁先看一眼，提高响应）
        with state['lock']:
            if state['status'] == ModelStatus.ROUTING.value:
                return True, f"模型 '{primary_name}' 已在运行"
            elif state['status'] == ModelStatus.STARTING.value:
                logger.info(f"模型 '{primary_name}' 正在启动中，等待完成...")
                return self._wait_for_model_startup(primary_name, state)

        # 获取启动锁
        logger.info(f"正在获取模型 '{primary_name}' 的启动锁...")
        lock_acquired = False
        try:
            lock_acquired = model_lock.acquire(blocking=True, timeout=60)
            if not lock_acquired:
                # 再次尝试非阻塞获取，防止极端情况
                if not model_lock.acquire(blocking=False):
                    return False, f"获取启动锁超时，请稍后重试: {primary_name}"
        except Exception as e:
            if lock_acquired:
                model_lock.release()
            return False, f"获取启动锁异常: {e}"

        try:
            logger.info(f"开始启动流程: {primary_name}")

            # =================================================================
            # 【修复点】: 删除了这里的 _check_if_cancelled 调用
            # 因为此时状态本身就是 STOPPED，不能视为被取消
            # =================================================================

            with state['lock']:
                current_status = state['status']

                # 再次确认状态（防止等待锁期间状态变了）
                if current_status == ModelStatus.ROUTING.value:
                    return True, f"模型 {primary_name} 已启动"
                elif current_status == ModelStatus.STARTING.value:
                    # 如果等待锁期间别人已经开始启动了
                    return self._wait_for_model_startup(primary_name, state)

                # 正常启动流程：将状态置为 STARTING
                # 这是启动的第一步，从此之后如果状态变回 STOPPED 才算被取消
                state['status'] = ModelStatus.STARTING.value
                state['failure_reason'] = None

            try:
                # 进入智能启动流程（内部包含后续的 Checkpoint 2/3/4/5/6）
                # 如果在后续过程中 stop_model 被调用，状态会变为 STOPPED，
                # 后续的 checkpoints 就会生效。
                success, message = self._start_model_intelligent(primary_name)

                # 【检查点 4 - 最终防线】启动完成后再次检查
                if self._check_if_cancelled(primary_name):
                    # 如果启动成功但被标记为停止，需要清理刚启动的进程
                    with state['lock']:
                        pid = state.get('pid')
                        if pid:
                            logger.warning(f"启动完成后检测到停止信号，清理进程 PID: {pid}")
                            self.process_manager.stop_process(f"model_{primary_name}", force=True)
                            self._reset_model_state(state)
                    return False, "启动完成后被立即停止"

                if success:
                    logger.info(f"模型 '{primary_name}' 启动成功，刷新设备状态缓存")
                    self.plugin_manager.update_device_status()

                return success, message
            except Exception as e:
                with state['lock']:
                    state['status'] = ModelStatus.FAILED.value
                    state['failure_reason'] = str(e)
                logger.error(f"启动模型 {primary_name} 失败: {e}", exc_info=True)
                return False, f"启动失败: {e}"

        finally:
            if lock_acquired:
                try:
                    model_lock.release()
                except Exception as e:
                    logger.error(f"释放启动锁异常: {e}")

    def _wait_for_model_startup(self, primary_name: str, state: Dict[str, Any]) -> Tuple[bool, str]:
        """等待模型启动完成（支持检测停止信号）"""
        wait_start = time.time()
        max_wait = 120
        check_interval = 0.5
        last_log = wait_start

        logger.info(f"开始等待模型 '{primary_name}' 启动，超时时间: {max_wait}s")

        while True:
            now = time.time()
            elapsed = now - wait_start

            if now - last_log >= 30:
                logger.info(f"模型 '{primary_name}' 仍在启动中... 已等待 {elapsed:.1f}s")
                last_log = now

            with state['lock']:
                status = state['status']
                fail_reason = state.get('failure_reason')

            if elapsed > max_wait:
                return False, f"启动超时 ({max_wait}s)"

            if status == ModelStatus.ROUTING.value:
                return True, f"模型 {primary_name} 已成功启动"
            elif status == ModelStatus.FAILED.value:
                msg = f"启动失败: {fail_reason}"
                logger.error(msg)
                return False, msg
            elif status == ModelStatus.STOPPED.value:
                # 【关键】检测到停止信号
                logger.info(f"检测到停止信号，放弃等待: {primary_name}")
                return False, "启动被用户中断"

            time.sleep(check_interval)

    def _start_model_intelligent(self, primary_name: str) -> Tuple[bool, str]:
        """
        智能启动：检查设备、自适应配置、资源释放

        优化：
        1. 使用缓存状态避免阻塞
        2. 支持无监控模式强制启动
        3. 适配 Linux 路径
        4. 增加取消检查点
        """
        state = self.models_state[primary_name]

        try:
            # 1. 确定在线设备（可能耗时）
            if self.config_manager.is_gpu_monitoring_disabled():
                logger.info("GPU监控禁用，忽略在线状态，使用所有可能设备")
                online_devices = set()
                base_conf = self.config_manager.get_model_config(primary_name)
                if base_conf:
                    # 尝试从配置提取所需设备，或回退到所有插件
                    for val in base_conf.values():
                        if isinstance(val, dict) and "required_devices" in val:
                            online_devices.update(val["required_devices"])
                if not online_devices:
                    online_devices = set(self.plugin_manager.get_all_device_plugins().keys())
            else:
                online_devices = self.plugin_manager.get_cached_online_devices()

            # 【检查点 2】设备检查后检查取消状态
            if self._check_if_cancelled(primary_name):
                return False, "启动被用户中断（设备检查后）"

            # 2. 获取自适应配置
            model_config = self.config_manager.get_adaptive_model_config(primary_name, online_devices)
            if not model_config:
                error_msg = f"无可用配置 (在线设备: {list(online_devices)})"
                with state['lock']:
                    state['status'] = ModelStatus.FAILED.value
                    state['failure_reason'] = error_msg
                logger.error(f"启动 '{primary_name}' 失败: {error_msg}")
                return False, error_msg

            state['current_config'] = model_config
            with state['lock']:
                state['status'] = ModelStatus.INIT_SCRIPT.value

            # 3. 资源检查与释放（可能很耗时）
            if not self._check_and_free_resources(model_config):
                error_msg = "设备资源不足，且无法通过释放空闲模型满足需求"
                with state['lock']:
                    state['status'] = ModelStatus.FAILED.value
                    state['failure_reason'] = error_msg
                logger.error(f"启动 '{primary_name}' 失败: {error_msg}")
                return False, error_msg

            # 【检查点 3】资源检查后检查取消状态
            if self._check_if_cancelled(primary_name):
                return False, "启动被用户中断（资源检查后）"

            # 4. 准备进程启动
            logger.info(f"启动进程: {primary_name} (脚本: {model_config['script_path']})")

            project_root = os.path.dirname(os.path.abspath(self.config_manager.config_path))
            process_name = f"model_{primary_name}"

            # 回调函数：捕获输出到日志管理器
            def output_callback(stream_type: str, message: str):
                self.log_manager.add_console_log(primary_name, message)

            # Windows 特定标志
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else None

            # 5. 执行启动
            success, msg, pid = self.process_manager.start_process(
                name=process_name,
                command=model_config['script_path'],  # Linux/Windows 适配路径
                cwd=project_root,
                description=f"模型进程: {primary_name}",
                shell=True,
                creation_flags=creation_flags,
                capture_output=True,
                output_callback=output_callback
            )

            if not success:
                with state['lock']:
                    state['status'] = ModelStatus.FAILED.value
                    state['failure_reason'] = msg
                return False, f"进程启动失败: {msg}"

            # 【检查点 4 - 关键】PID 生成后立即检查
            # 此时进程已经启动，如果需要停止必须终止它
            with state['lock']:
                if state['status'] == ModelStatus.STOPPED.value:
                    logger.warning(f"进程刚启动 (PID: {pid}) 即被要求停止，立即终止")
                    self.process_manager.stop_process(f"model_{primary_name}", force=True)
                    return False, "启动被中断（进程已终止）"

                # 正常情况：更新状态和 PID
                state['process'] = None  # 这里的 process 引用由 process_manager 管理
                state['pid'] = pid
                state['log_thread'] = None

            # 6. 健康检查（长耗时操作）
            return self._perform_health_checks(primary_name, model_config)

        except Exception as e:
            logger.error(f"智能启动异常: {e}", exc_info=True)
            with state['lock']:
                state['status'] = ModelStatus.FAILED.value
                state['failure_reason'] = str(e)
            return False, f"启动异常: {e}"

    def _check_and_free_resources(self, model_config: Dict[str, Any]) -> bool:
        """检查资源，如果不足则尝试循环释放空闲模型"""
        if self.config_manager.is_gpu_monitoring_disabled():
            return True

        while True:
            resource_ok = True
            deficit_devices = {}
            device_status = self.plugin_manager.get_device_status_snapshot()
            required_mem = model_config.get("memory_mb", {})

            for dev_name, req_mb in required_mem.items():
                status = device_status.get(dev_name)
                if not status or not status.get('online', False):
                    logger.warning(f"依赖设备 '{dev_name}' 不在线")
                    return False

                info = status.get('info', {})
                avail_mb = info.get('available_memory_mb', 0)
                
                if avail_mb < req_mb:
                    deficit_devices[dev_name] = req_mb - avail_mb
                    resource_ok = False

            if resource_ok:
                logger.info("资源检查通过")
                return True

            logger.warning(f"资源不足: {deficit_devices}，尝试释放空闲模型...")
            
            # 尝试停止一个模型，如果成功则 continue 再次检查，否则 break
            if self._stop_idle_models_for_resources(deficit_devices):
                continue
            else:
                logger.error("无法释放足够资源")
                break

        return False

    def _stop_idle_models_for_resources(self, deficit_devices: Dict[str, int]) -> bool:
        """选择并停止一个最佳的空闲模型以释放资源"""
        candidates = []
        now = time.time()

        for name, state in self.models_state.items():
            with state['lock']:
                if state['status'] != ModelStatus.ROUTING.value:
                    continue

                # 如果有待处理请求，跳过
                if self.api_router and self.api_router.pending_requests.get(name, 0) > 0:
                    continue
                
                config = state.get('current_config')
                if not config:
                    continue
                
                # 检查是否使用了需要释放的设备
                used_devs = set(config.get('required_devices', []) or config.get('memory_mb', {}).keys())
                if used_devs.isdisjoint(set(deficit_devices.keys())):
                    continue
                
                # 计算评分 (空闲时间 / 占用显存)
                last_access = state.get('last_access') or 0
                idle_sec = max(0, now - last_access)
                mem_mb = sum(config.get('memory_mb', {}).values())
                mem_gb = max(0.5, mem_mb / 1024.0)
                score = idle_sec / mem_gb
                
                candidates.append({
                    "name": name,
                    "score": score,
                    "idle": idle_sec,
                    "mem": mem_gb
                })

        if not candidates:
            return False

        # 按分数降序排序，停止分数最高的
        target = sorted(candidates, key=lambda x: x['score'], reverse=True)[0]
        logger.info(f"为释放资源停止模型: {target['name']} (空闲: {target['idle']:.0f}s, 显存: {target['mem']:.2f}GB)")
        
        success, _ = self.stop_model(target['name'])
        return success

    def _perform_health_checks(self, primary_name: str, model_config: Dict[str, Any]) -> Tuple[bool, str]:
        """执行模型健康检查（带取消检查）"""
        state = self.models_state[primary_name]
        port = model_config['port']
        timeout = 300
        start_ts = time.time()

        with state['lock']:
            state['status'] = ModelStatus.HEALTH_CHECK.value

        logger.info(f"开始健康检查: {primary_name} (端口: {port})")

        mode = model_config.get("mode", "Chat")
        plugin = self.plugin_manager.get_interface_plugin(mode)

        if not plugin:
            msg = f"未找到模式 '{mode}' 的接口插件"
            logger.error(msg)
            self._handle_startup_failure(primary_name, msg)
            return False, msg

        # 【检查点 5】健康检查前检查
        if self._check_if_cancelled(primary_name):
            return False, "启动被中断（健康检查前）"

        # 执行健康检查（可能需要修改插件支持定期检查状态）
        # 如果插件不支持中断，我们至少在检查前验证一次
        ok, msg = plugin.health_check(primary_name, port, start_ts, timeout)

        # 【检查点 6】健康检查后检查
        if self._check_if_cancelled(primary_name):
            return False, "启动被中断（健康检查后）"

        if ok:
            logger.info(f"模型 '{primary_name}' 健康检查通过")
            with state['lock']:
                state['status'] = ModelStatus.ROUTING.value
                state['last_access'] = time.time()

            self.runtime_monitor.record_model_start(primary_name)
            return True, "启动成功"
        else:
            self._handle_startup_failure(primary_name, msg)
            return False, f"健康检查失败: {msg}"

    def _handle_startup_failure(self, primary_name: str, reason: str):
        """处理启动失败：更新状态并停止进程"""
        logger.error(f"模型 '{primary_name}' 启动失败: {reason}")
        state = self.models_state[primary_name]
        with state['lock']:
            state['status'] = ModelStatus.FAILED.value
            state['failure_reason'] = reason
        self.stop_model(primary_name)

    def stop_model(self, primary_name: str, refresh_cache: bool = True) -> Tuple[bool, str]:
        """
        停止模型（支持中断启动中的流程）

        设计：
        - 不等待启动锁，立即设置停止信号
        - 如果进程已存在（有PID），直接终止
        - 如果进程不存在（启动中），依赖启动线程的检查点自行中断

        Args:
            primary_name: 模型名称
            refresh_cache: 停止后是否刷新设备状态缓存

        Returns:
            (是否成功, 消息)
        """
        state = self.models_state[primary_name]

        logger.info(f"收到停止请求: {primary_name}")

        # 步骤1：无条件设置停止信号（不获取启动锁）
        with state['lock']:
            current_status = state['status']

            if current_status in [ModelStatus.STOPPED.value, ModelStatus.FAILED.value]:
                return True, "模型已停止"

            # 【核心】设置停止信号，启动线程会在检查点检测到
            state['status'] = ModelStatus.STOPPED.value
            state['failure_reason'] = "被用户请求停止"

            # 获取 PID（可能为 None，如果还在启动中）
            pid = state.get('pid')

        # 步骤2：终止已存在的进程（如果有）
        stopped_process = False
        if pid:
            proc_name = f"model_{primary_name}"
            success, msg = self.process_manager.stop_process(proc_name, force=True, timeout=3)
            if success:
                logger.info(f"进程已终止: PID {pid}")
                stopped_process = True
            else:
                logger.warning(f"进程终止失败: {msg}")

        # 步骤3：清理状态（不管是否还在启动，都强制清理）
        with state['lock']:
            self._reset_model_state(state)

        # 步骤4：记录监控和刷新缓存
        if self.runtime_monitor.is_model_monitored(primary_name):
            self.runtime_monitor.record_model_stop(primary_name)

        if refresh_cache:
            self.plugin_manager.update_device_status()

        # 步骤5：根据是否终止进程返回不同消息
        if stopped_process:
            return True, "模型已停止"
        else:
            return True, "已发送停止信号，正在中断启动流程..."

    def _reset_model_state(self, state: Dict[str, Any]):
        """重置模型状态字典"""
        state.update({
            "process": None,
            "pid": None,
            "status": ModelStatus.STOPPED.value,
            "last_access": None,
            "log_thread": None,
            "current_config": None,
            "failure_reason": None
        })

    def unload_all_models(self):
        """
        并行卸载所有模型 (防死锁优化)
        
        Returns:
            成功停止的模型数量
        """
        logger.info("开始卸载所有运行中的模型...")
        models_to_stop = []

        # 1. 筛选需要停止的模型
        for name, state in self.models_state.items():
            with state['lock']:
                if state['status'] not in [ModelStatus.STOPPED.value, ModelStatus.FAILED.value]:
                    models_to_stop.append(name)

        if not models_to_stop:
            logger.info("没有运行中的模型")
            return 0

        # 2. 并行停止
        def stop_task(name):
            try:
                # 批量停止时不刷新缓存，最后统一刷新
                ok, msg = self.stop_model(name, refresh_cache=False)
                return name, ok, msg
            except Exception as e:
                return name, False, str(e)

        futures = [self.executor.submit(stop_task, name) for name in models_to_stop]
        stopped_count = 0
        
        # 3. 等待结果
        timeout = len(models_to_stop) * 5 + 10
        for future in concurrent.futures.as_completed(futures, timeout=timeout):
            try:
                name, ok, msg = future.result()
                if ok:
                    stopped_count += 1
                else:
                    logger.warning(f"模型 '{name}' 卸载失败: {msg}")
            except Exception as e:
                logger.error(f"处理卸载结果异常: {e}")

        # 4. 统一刷新缓存
        if stopped_count > 0:
            self.plugin_manager.update_device_status()

        logger.info(f"全部卸载完成，成功: {stopped_count}/{len(models_to_stop)}")
        return stopped_count

    def idle_check_loop(self):
        """
        空闲检查循环
        
        策略：
        - 仅在模型处于 ROUTING 状态时检查
        - 结合 `last_access` 时间和当前 API `pending_requests` 计数
        - 避免在有请求处理时关闭模型
        """
        while self.is_running:
            time.sleep(30)
            if not self.is_running:
                break

            try:
                alive_min = self.config_manager.get_alive_time()
                if alive_min <= 0:
                    continue

                alive_sec = alive_min * 60
                now = time.time()
                to_stop = []

                for name, state in self.models_state.items():
                    with state['lock']:
                        if state['status'] != ModelStatus.ROUTING.value:
                            continue
                        
                        last = state.get('last_access')
                        if not last:
                            continue

                        pending = self.api_router.pending_requests.get(name, 0) if self.api_router else 0
                        
                        if pending == 0 and (now - last > alive_sec):
                            to_stop.append(name)

                for name in to_stop:
                    # 双重检查：防止停止前一刻有新请求
                    if self.api_router and self.api_router.pending_requests.get(name, 0) > 0:
                        logger.info(f"模型 {name} 收到新请求，取消自动关闭")
                        continue
                    
                    logger.info(f"触发自动关闭策略: {name}")
                    self.stop_model(name)

            except Exception as e:
                logger.error(f"空闲检查线程异常: {e}")

    def get_all_models_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有模型详细状态"""
        status_map = {}
        now = time.time()
        online_devs = self.plugin_manager.get_cached_online_devices()

        for name, state in self.models_state.items():
            last = state.get('last_access')
            idle_str = f"{now - last:.0f}" if last else "N/A"
            
            conf = self.config_manager.get_model_config(name)
            adaptive = self.config_manager.get_adaptive_model_config(name, online_devs)

            status_map[name] = {
                "aliases": conf.get("aliases", [name]) if conf else [name],
                "status": state['status'],
                "pid": state['pid'],
                "idle_time_sec": idle_str,
                "mode": conf.get("mode", "Chat") if conf else "Chat",
                "is_available": bool(adaptive),
                "current_script_path": adaptive.get("script_path", "") if adaptive else "无可用配置",
                "config_source": adaptive.get("config_source", "N/A") if adaptive else "N/A",
                "failure_reason": state.get('failure_reason')
            }
        return status_map

    def get_model_logs(self, primary_name: str) -> List[Dict[str, Any]]:
        try:
            return self.log_manager.get_logs(primary_name)
        except Exception as e:
            logger.error(f"获取日志失败: {e}")
            return [{"timestamp": time.time(), "message": f"错误: {e}"}]

    def subscribe_to_model_logs(self, primary_name: str) -> queue.Queue:
        return self.log_manager.subscribe_to_logs(primary_name)

    def unsubscribe_from_model_logs(self, primary_name: str, subscriber_queue: queue.Queue):
        self.log_manager.unsubscribe_from_logs(primary_name, subscriber_queue)

    def get_log_stats(self) -> Dict[str, Any]:
        return self.log_manager.get_log_stats()

    def get_model_list(self) -> Dict[str, Any]:
        """获取符合 OpenAI 格式的模型列表"""
        data = []
        for name in self.models_state.keys():
            conf = self.config_manager.get_model_config(name)
            if conf:
                data.append({
                    "id": name,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "user",
                    "aliases": conf.get("aliases", []),
                    "mode": conf.get("mode", "Chat")
                })
        return {"object": "list", "data": data}

    def shutdown(self):
        """
        关闭模型控制器
        
        1. 停止监控
        2. 取消启动任务
        3. 并行终止所有模型进程
        4. 清理资源
        """
        logger.info("正在关闭模型控制器...")
        self.is_running = False
        self.shutdown_event.set()

        if self.plugin_manager:
            self.plugin_manager.stop_monitor()

        # 取消等待中的启动任务
        for name, future in self.startup_futures.items():
            if not future.done():
                future.cancel()

        # 并行终止进程
        stop_tasks = []
        for name, state in self.models_state.items():
            with state['lock']:
                if state.get('pid'):
                    stop_tasks.append((name, f"model_{name}"))

        def kill_task(name, proc_name):
            try:
                ok, msg = self.process_manager.stop_process(proc_name, force=True, timeout=3)
                return name, ok, msg
            except Exception as e:
                return name, False, str(e)

        futures = [self.executor.submit(kill_task, n, p) for n, p in stop_tasks]
        terminated = []
        
        timeout = len(stop_tasks) * 2 + 5
        try:
            for f in concurrent.futures.as_completed(futures, timeout=timeout):
                name, ok, msg = f.result()
                if ok:
                    terminated.append(name)
                    with self.models_state[name]['lock']:
                        self._reset_model_state(self.models_state[name])
                else:
                    logger.warning(f"终止模型 {name} 失败: {msg}")
        except concurrent.futures.TimeoutError:
            logger.error("关闭过程超时")

        # 记录停止时间
        for name in self.models_state:
            if self.runtime_monitor.is_model_monitored(name):
                self.runtime_monitor.record_model_stop(name)

        self.runtime_monitor.shutdown()
        self.log_manager.shutdown()
        self.executor.shutdown(wait=True)

        logger.info(f"控制器关闭完成，已清理 {len(terminated)} 个模型进程")