"""
模型控制器 (Model Controller)
===========================

负责模型的全生命周期管理，核心功能包括：
1. 模型进程的启动与停止（支持并行启动）。
2. 显存资源的智能管理（自动计算资源需求，循环释放空闲模型）。
3. 进程守护与僵尸进程清理。
4. 日志流的实时捕获与分发。
5. 跨平台兼容性处理（Windows/Linux）。

关键特性：
- 使用 RLock 解决自动关闭时的死锁问题。
- 引入 Checkpoint 机制，支持启动过程中的立即中断。
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
    STOPPED = "stopped"          # 已停止
    STARTING = "starting"        # 正在获取锁或准备启动
    INIT_SCRIPT = "init_script"  # 正在执行启动脚本
    HEALTH_CHECK = "health_check"# 正在进行健康检查
    ROUTING = "routing"          # 运行中且可路由
    FAILED = "failed"            # 启动失败或运行异常


class LogManager:
    """
    日志管理器
    
    负责模型控制台输出的收集、存储、实时流推送以及定期清理。
    """

    def __init__(self):
        self.model_logs: Dict[str, List[Dict[str, Any]]] = {}
        self.log_subscribers: Dict[str, List[queue.Queue]] = {}
        self.log_locks: Dict[str, threading.Lock] = {}
        self.global_lock = threading.Lock()
        self._running = True
        logger.info("日志管理器初始化完成")

    def register_model(self, model_name: str):
        """为新模型初始化日志存储结构"""
        with self.global_lock:
            if model_name not in self.model_logs:
                self.model_logs[model_name] = []
                self.log_subscribers[model_name] = []
                self.log_locks[model_name] = threading.Lock()
                logger.debug(f"已注册模型日志空间: {model_name}")

    def unregister_model(self, model_name: str):
        """注销模型并清理相关订阅资源"""
        with self.global_lock:
            if model_name in self.model_logs:
                # 向所有订阅者发送结束信号
                for subscriber_queue in self.log_subscribers[model_name]:
                    try:
                        subscriber_queue.put(None)
                    except Exception:
                        pass

                del self.model_logs[model_name]
                del self.log_subscribers[model_name]
                del self.log_locks[model_name]
                logger.debug(f"已注销模型日志空间: {model_name}")

    def add_console_log(self, model_name: str, message: str):
        """添加一条控制台日志并广播给订阅者"""
        if model_name not in self.model_logs:
            self.register_model(model_name)

        log_entry = {
            "timestamp": time.time(),
            "message": message
        }

        # 写入存储
        with self.log_locks[model_name]:
            self.model_logs[model_name].append(log_entry)

        # 广播推送
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
        """获取所有模型的日志快照"""
        with self.global_lock:
            result = {}
            for model_name in self.model_logs:
                result[model_name] = self.get_logs(model_name)
            return result

    def clear_logs(self, model_name: str):
        """手动清空指定模型的日志"""
        if model_name in self.model_logs:
            with self.log_locks[model_name]:
                self.model_logs[model_name].clear()
            logger.debug(f"已手动清空模型日志: {model_name}")

    def cleanup_old_logs(self, model_name: str, keep_minutes: int) -> int:
        """
        清理过期日志
        
        Args:
            keep_minutes: 保留最近 N 分钟的日志
        Returns:
            删除的条目数
        """
        if model_name not in self.model_logs:
            return 0

        cutoff_time = time.time() - (keep_minutes * 60)

        with self.log_locks[model_name]:
            original_count = len(self.model_logs[model_name])
            self.model_logs[model_name] = [
                log for log in self.model_logs[model_name]
                if log['timestamp'] > cutoff_time
            ]
            removed_count = original_count - len(self.model_logs[model_name])

        if removed_count > 0:
            logger.debug(f"日志自动清理 [{model_name}]: 移除了 {removed_count} 条过期日志 (保留最近 {keep_minutes} 分钟)")
        return removed_count

    def subscribe_to_logs(self, model_name: str) -> queue.Queue:
        """订阅实时日志流"""
        if model_name not in self.model_logs:
            self.register_model(model_name)

        subscriber_queue = queue.Queue()
        with self.global_lock:
            self.log_subscribers[model_name].append(subscriber_queue)

        logger.debug(f"新增日志订阅者: {model_name}")
        return subscriber_queue

    def unsubscribe_from_logs(self, model_name: str, subscriber_queue: queue.Queue):
        """取消订阅"""
        if model_name in self.log_subscribers:
            with self.global_lock:
                if subscriber_queue in self.log_subscribers[model_name]:
                    self.log_subscribers[model_name].remove(subscriber_queue)
                    logger.debug(f"移除日志订阅者: {model_name}")

    def _notify_subscribers(self, model_name: str, log_entry: Dict[str, Any]):
        """内部方法：向所有活跃订阅者广播日志"""
        if model_name not in self.log_subscribers:
            return

        # 复制列表以避免迭代时修改
        subscribers_copy = self.log_subscribers[model_name].copy()
        for subscriber_queue in subscribers_copy:
            try:
                subscriber_queue.put_nowait(log_entry)
            except queue.Full:
                self._remove_subscriber(model_name, subscriber_queue, reason="队列已满")
            except Exception:
                self._remove_subscriber(model_name, subscriber_queue, reason="连接异常")

    def _remove_subscriber(self, model_name: str, subscriber_queue: queue.Queue, reason: str):
        """内部方法：清理无效订阅者"""
        with self.global_lock:
            if subscriber_queue in self.log_subscribers.get(model_name, []):
                self.log_subscribers[model_name].remove(subscriber_queue)
                logger.debug(f"强制移除订阅者 ({reason}): {model_name}")

    def get_log_stats(self) -> Dict[str, Any]:
        """获取日志系统统计信息"""
        with self.global_lock:
            stats = {
                "total_models": len(self.model_logs),
                "total_entries": sum(len(logs) for logs in self.model_logs.values()),
                "total_subscribers": sum(len(subs) for subs in self.log_subscribers.values()),
                "details": {}
            }
            for model_name, logs in self.model_logs.items():
                stats["details"][model_name] = {
                    "count": len(logs),
                    "subscribers": len(self.log_subscribers[model_name])
                }
            return stats

    def shutdown(self):
        """关闭日志管理器并断开所有连接"""
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
    
    负责记录模型的启动/停止时间，并定期同步到数据库。
    """

    def __init__(self, db_path: Optional[str] = None):
        self.monitor = Monitor(db_path)
        self.active_models: Dict[str, threading.Timer] = {}
        self.lock = threading.Lock()
        logger.info("模型运行监控器初始化完成")

    def record_model_start(self, model_name: str) -> bool:
        """记录启动时间并开启心跳更新"""
        start_time = time.time()
        try:
            self.monitor.add_model_runtime_start(model_name, start_time)
            logger.info(f"已记录启动时间: {model_name}")
        except Exception as e:
            logger.error(f"记录启动时间失败 [{model_name}]: {e}")
            return False

        with self.lock:
            if model_name in self.active_models:
                self.active_models[model_name].cancel()

            # 启动心跳定时器（每10秒更新一次结束时间）
            timer = threading.Timer(10.0, self._update_runtime_periodically, args=[model_name])
            timer.daemon = True
            timer.start()
            self.active_models[model_name] = timer

        return True

    def _update_runtime_periodically(self, model_name: str):
        """周期性更新数据库中的结束时间（心跳机制）"""
        with self.lock:
            if model_name in self.active_models:
                end_time = time.time()
                try:
                    self.monitor.update_model_runtime_end(model_name, end_time)
                except Exception as e:
                    logger.error(f"更新运行时间失败 [{model_name}]: {e}")

                # 续约定时器
                timer = threading.Timer(10.0, self._update_runtime_periodically, args=[model_name])
                timer.daemon = True
                timer.start()
                self.active_models[model_name] = timer

    def record_model_stop(self, model_name: str) -> bool:
        """记录停止时间并终止心跳"""
        end_time = time.time()
        with self.lock:
            if model_name in self.active_models:
                self.active_models[model_name].cancel()
                del self.active_models[model_name]

        try:
            self.monitor.update_model_runtime_end(model_name, end_time)
            logger.info(f"已记录停止时间: {model_name}")
            return True
        except Exception as e:
            logger.error(f"记录停止时间失败 [{model_name}]: {e}")
            return False

    def is_model_monitored(self, model_name: str) -> bool:
        with self.lock:
            return model_name in self.active_models

    def shutdown(self):
        """停止所有监控任务"""
        with self.lock:
            for name, timer in self.active_models.items():
                timer.cancel()
            self.active_models.clear()

        try:
            self.monitor.close()
            logger.info("模型运行监控器已关闭")
        except Exception as e:
            logger.error(f"关闭监控器异常: {e}")

    def __del__(self):
        try:
            self.shutdown()
        except Exception:
            pass


class ModelController:
    """
    模型控制器核心类
    
    协调 ConfigManager, PluginManager, ProcessManager 实现模型的智能调度。
    """

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.models_state: Dict[str, Dict[str, Any]] = {}
        self.is_running = True
        
        # 核心组件初始化
        self.plugin_manager: Optional[PluginManager] = None
        self.process_manager = get_process_manager()
        self.runtime_monitor = ModelRuntimeMonitor()
        self.log_manager = LogManager()
        
        # 线程池与并发控制
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=5)
        self.startup_futures: Dict[str, concurrent.futures.Future] = {}
        self.startup_locks: Dict[str, threading.Lock] = {}
        self.shutdown_event = threading.Event()
        
        # 外部依赖
        self.api_router: Optional[Any] = None

        # 启动空闲资源检查守护线程
        self.idle_check_thread = threading.Thread(target=self.idle_check_loop, daemon=True)
        self.idle_check_thread.start()

        # 初始化状态字典
        self._init_models_state()
        
        # 加载硬件与接口插件
        self.load_plugins()

    def _init_models_state(self):
        """初始化模型状态表"""
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
                    # 【核心修复】使用 RLock 允许同一线程（如 auto-stop 逻辑）重入锁，防止死锁
                    "lock": threading.RLock(),
                    "log_thread": None,
                    "current_config": None,
                    "failure_reason": None
                }
                # 启动专用互斥锁
                self.startup_locks[primary_name] = threading.Lock()
                self.log_manager.register_model(primary_name)

        self.models_state = new_states

    def set_api_router(self, api_router: Any):
        """注入 API Router 以获取请求统计"""
        self.api_router = api_router
        logger.info("API Router 已注入 Model Controller")

    def load_plugins(self):
        """加载插件并刷新初始设备状态"""
        device_dir = self.config_manager.get_device_plugin_dir()
        interface_dir = self.config_manager.get_interface_plugin_dir()
        self.plugin_manager = PluginManager(device_dir, interface_dir)

        try:
            self.plugin_manager.load_all_plugins(model_manager=self)
            logger.info(f"插件加载完毕: 设备插件 {len(self.plugin_manager.get_all_device_plugins())} 个, 接口插件 {len(self.plugin_manager.get_all_interface_plugins())} 个")
            
            logger.info("初始化设备状态缓存...")
            self.plugin_manager.update_device_status()
            
            online_devs = self.plugin_manager.get_cached_online_devices()
            logger.info(f"在线设备检测结果: {list(online_devs) if online_devs else '无'}")

        except Exception as e:
            logger.error(f"插件加载失败: {e}")

    def _check_if_cancelled(self, primary_name: str) -> bool:
        """
        [Checkpoint] 检查启动流程是否已被用户取消
        """
        state = self.models_state[primary_name]

        with state['lock']:
            cancelled = state['status'] == ModelStatus.STOPPED.value
            if cancelled:
                logger.info(f"检测到取消信号，终止启动流程: {primary_name}")
                state['failure_reason'] = "启动被用户中断"

        return cancelled

    def start_auto_start_models(self):
        """批量启动配置为自动启动的模型"""
        logger.info("正在扫描自动启动配置...")

        online_devices = self.plugin_manager.get_cached_online_devices()
        
        # 若无设备且未禁用监控，则跳过
        if not online_devices and not self.config_manager.is_gpu_monitoring_disabled():
            logger.warning("未检测到在线设备，跳过自动启动")
            return

        auto_start_models = [
            name for name in self.config_manager.get_model_names()
            if self.config_manager.is_auto_start(name)
        ]

        if not auto_start_models:
            logger.info("无自动启动模型")
            return

        logger.info(f"准备并行启动 {len(auto_start_models)} 个模型: {auto_start_models}")

        def start_single_model(model_name):
            try:
                success, msg = self.start_model(model_name)
                return model_name, success, msg
            except Exception as ex:
                logger.error(f"自动启动异常 [{model_name}]: {ex}")
                return model_name, False, f"异常: {ex}"

        futures = []
        for model_name in auto_start_models:
            futures.append(self.executor.submit(start_single_model, model_name))

        started_count = 0
        for future in concurrent.futures.as_completed(futures, timeout=120):
            try:
                name, success, msg = future.result()
                if success:
                    started_count += 1
                else:
                    logger.warning(f"自动启动失败 [{name}]: {msg}")
            except Exception as e:
                logger.error(f"处理启动结果异常: {e}")

        logger.info(f"自动启动流程完成: 成功 {started_count}/{len(auto_start_models)}")

    def start_model(self, primary_name: str) -> Tuple[bool, str]:
        """
        启动模型（线程安全，支持重入与中断）
        
        Returns:
            (is_success, message)
        """
        state = self.models_state[primary_name]
        model_lock = self.startup_locks[primary_name]

        # 1. 快速状态检查（无锁预览）
        with state['lock']:
            if state['status'] == ModelStatus.ROUTING.value:
                return True, f"模型 '{primary_name}' 已在运行"
            elif state['status'] == ModelStatus.STARTING.value:
                logger.info(f"模型 '{primary_name}' 正在启动中，进入等待模式...")
                return self._wait_for_model_startup(primary_name, state)

        # 2. 获取启动互斥锁
        logger.info(f"尝试获取启动锁: {primary_name}")
        lock_acquired = False
        try:
            lock_acquired = model_lock.acquire(blocking=True, timeout=60)
            if not lock_acquired:
                # 再次非阻塞尝试，防止边界情况
                if not model_lock.acquire(blocking=False):
                    return False, f"获取启动锁超时: {primary_name}"
        except Exception as e:
            if lock_acquired:
                model_lock.release()
            return False, f"锁获取异常: {e}"

        try:
            logger.info(f"获得锁，开始启动流程: {primary_name}")

            with state['lock']:
                current_status = state['status']

                # 双重检查
                if current_status == ModelStatus.ROUTING.value:
                    return True, f"模型已就绪"
                elif current_status == ModelStatus.STARTING.value:
                    return self._wait_for_model_startup(primary_name, state)

                # 标记状态为启动中
                state['status'] = ModelStatus.STARTING.value
                state['failure_reason'] = None

            try:
                # 执行智能启动（包含 Checkpoint 机制）
                success, message = self._start_model_intelligent(primary_name)

                # [Checkpoint 4] 最终防线
                if self._check_if_cancelled(primary_name):
                    with state['lock']:
                        pid = state.get('pid')
                        if pid:
                            logger.warning(f"启动完成但收到停止信号，清理残留进程 PID: {pid}")
                            self.process_manager.stop_process(f"model_{primary_name}", force=True)
                            self._reset_model_state(state)
                    return False, "启动完成后被立即停止"

                if success:
                    logger.info(f"模型 '{primary_name}' 启动成功，刷新缓存")
                    self.plugin_manager.update_device_status()

                return success, message
            except Exception as e:
                with state['lock']:
                    state['status'] = ModelStatus.FAILED.value
                    state['failure_reason'] = str(e)
                logger.error(f"启动过程严重异常 [{primary_name}]: {e}", exc_info=True)
                return False, f"启动异常: {e}"

        finally:
            if lock_acquired:
                try:
                    model_lock.release()
                except Exception as e:
                    logger.error(f"释放启动锁异常: {e}")

    def _wait_for_model_startup(self, primary_name: str, state: Dict[str, Any]) -> Tuple[bool, str]:
        """等待其他线程的启动结果"""
        wait_start = time.time()
        max_wait = 120
        check_interval = 0.5
        last_log = wait_start

        logger.info(f"等待模型启动: {primary_name} (超时: {max_wait}s)")

        while True:
            now = time.time()
            elapsed = now - wait_start

            if now - last_log >= 30:
                logger.info(f"等待中... {primary_name} ({elapsed:.1f}s)")
                last_log = now

            with state['lock']:
                status = state['status']
                fail_reason = state.get('failure_reason')

            if elapsed > max_wait:
                return False, f"等待超时"

            if status == ModelStatus.ROUTING.value:
                return True, f"模型启动成功"
            elif status == ModelStatus.FAILED.value:
                return False, f"启动失败: {fail_reason}"
            elif status == ModelStatus.STOPPED.value:
                logger.info(f"等待期间检测到停止信号: {primary_name}")
                return False, "启动被用户中断"

            time.sleep(check_interval)

    def _start_model_intelligent(self, primary_name: str) -> Tuple[bool, str]:
        """
        智能启动流程
        
        流程：
        1. 检查在线设备。
        2. 获取适配配置。
        3. 检查资源（必要时释放空闲模型）。
        4. 启动进程。
        5. 健康检查。
        """
        state = self.models_state[primary_name]

        try:
            # 1. 确定可用设备
            if self.config_manager.is_gpu_monitoring_disabled():
                logger.info("GPU监控已禁用，尝试使用配置文件中的所有设备")
                online_devices = set()
                base_conf = self.config_manager.get_model_config(primary_name)
                if base_conf:
                    for val in base_conf.values():
                        if isinstance(val, dict) and "required_devices" in val:
                            online_devices.update(val["required_devices"])
                if not online_devices:
                    online_devices = set(self.plugin_manager.get_all_device_plugins().keys())
            else:
                online_devices = self.plugin_manager.get_cached_online_devices()

            # [Checkpoint 2] 设备检查后
            if self._check_if_cancelled(primary_name):
                return False, "启动中断（阶段2）"

            # 2. 获取自适应配置
            model_config = self.config_manager.get_adaptive_model_config(primary_name, online_devices)
            if not model_config:
                error_msg = f"无适配配置 (可用设备: {list(online_devices)})"
                with state['lock']:
                    state['status'] = ModelStatus.FAILED.value
                    state['failure_reason'] = error_msg
                logger.error(f"启动失败: {error_msg}")
                return False, error_msg

            state['current_config'] = model_config
            with state['lock']:
                state['status'] = ModelStatus.INIT_SCRIPT.value

            # 3. 资源管理（关键路径）
            if not self._check_and_free_resources(model_config):
                error_msg = "资源不足且无法通过释放腾出空间"
                with state['lock']:
                    state['status'] = ModelStatus.FAILED.value
                    state['failure_reason'] = error_msg
                logger.error(f"启动失败: {error_msg}")
                return False, error_msg

            # [Checkpoint 3] 资源检查后
            if self._check_if_cancelled(primary_name):
                return False, "启动中断（阶段3）"

            # 4. 启动进程
            logger.info(f"执行启动脚本: {model_config['script_path']}")

            project_root = os.path.dirname(os.path.abspath(self.config_manager.config_path))
            process_name = f"model_{primary_name}"

            # 日志回调
            def output_callback(stream_type: str, message: str):
                self.log_manager.add_console_log(primary_name, message)

            # Windows 进程组标志
            creation_flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else None

            success, msg, pid = self.process_manager.start_process(
                name=process_name,
                command=model_config['script_path'],
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

            # [Checkpoint 4] 进程启动后立即检查
            with state['lock']:
                if state['status'] == ModelStatus.STOPPED.value:
                    logger.warning(f"进程已启动(PID {pid})但收到停止请求，立即终止")
                    self.process_manager.stop_process(f"model_{primary_name}", force=True)
                    return False, "启动中断（进程已终止）"

                state['process'] = None
                state['pid'] = pid
                state['log_thread'] = None

            # 5. 健康检查
            return self._perform_health_checks(primary_name, model_config)

        except Exception as e:
            logger.error(f"智能启动流程异常: {e}", exc_info=True)
            with state['lock']:
                state['status'] = ModelStatus.FAILED.value
                state['failure_reason'] = str(e)
            return False, f"启动异常: {e}"

    def _check_and_free_resources(self, model_config: Dict[str, Any]) -> bool:
        """检查资源是否满足，不足则尝试循环释放低优先级的模型"""
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
                logger.debug("资源检查通过")
                return True

            logger.warning(f"资源不足 {deficit_devices}，尝试释放空闲模型...")
            
            # 尝试停止一个模型，成功则 continue 重新检查，失败则 break
            if self._stop_idle_models_for_resources(deficit_devices):
                continue
            else:
                logger.error("无法释放足够的资源")
                break

        return False

    def _stop_idle_models_for_resources(self, deficit_devices: Dict[str, int]) -> bool:
        """
        寻找并停止最合适的空闲模型
        
        评分策略: 空闲时间 / 占用显存 (单位GB)
        """
        candidates = []
        now = time.time()

        for name, state in self.models_state.items():
            with state['lock']:
                if state['status'] != ModelStatus.ROUTING.value:
                    continue

                # 避开正在处理请求的模型
                if self.api_router and self.api_router.pending_requests.get(name, 0) > 0:
                    continue
                
                config = state.get('current_config')
                if not config:
                    continue
                
                # 仅考虑占用了所需资源设备的模型
                used_devs = set(config.get('required_devices', []) or config.get('memory_mb', {}).keys())
                if used_devs.isdisjoint(set(deficit_devices.keys())):
                    continue
                
                # 计算评分
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

        # 停止得分最高的模型
        target = sorted(candidates, key=lambda x: x['score'], reverse=True)[0]
        logger.info(f"释放资源: 停止模型 '{target['name']}' (空闲: {target['idle']:.0f}s, 显存: {target['mem']:.2f}GB)")
        
        success, _ = self.stop_model(target['name'])
        return success

    def _perform_health_checks(self, primary_name: str, model_config: Dict[str, Any]) -> Tuple[bool, str]:
        """执行健康检查（含中断检测）"""
        state = self.models_state[primary_name]
        port = model_config['port']
        timeout = 300
        start_ts = time.time()

        with state['lock']:
            state['status'] = ModelStatus.HEALTH_CHECK.value

        logger.info(f"开始健康检查: {primary_name} (Port: {port})")

        mode = model_config.get("mode", "Chat")
        plugin = self.plugin_manager.get_interface_plugin(mode)

        if not plugin:
            msg = f"未找到模式 '{mode}' 的接口插件"
            self._handle_startup_failure(primary_name, msg)
            return False, msg

        # [Checkpoint 5] 检查前
        if self._check_if_cancelled(primary_name):
            return False, "启动中断（阶段5）"

        # 调用插件进行检查
        ok, msg = plugin.health_check(primary_name, port, start_ts, timeout)

        # [Checkpoint 6] 检查后
        if self._check_if_cancelled(primary_name):
            return False, "启动中断（阶段6）"

        if ok:
            logger.info(f"健康检查通过: {primary_name}")
            with state['lock']:
                state['status'] = ModelStatus.ROUTING.value
                state['last_access'] = time.time()

            self.runtime_monitor.record_model_start(primary_name)
            return True, "启动成功"
        else:
            self._handle_startup_failure(primary_name, msg)
            return False, f"健康检查失败: {msg}"

    def _handle_startup_failure(self, primary_name: str, reason: str):
        """处理启动失败逻辑"""
        logger.error(f"启动失败清理 [{primary_name}]: {reason}")
        state = self.models_state[primary_name]
        with state['lock']:
            state['status'] = ModelStatus.FAILED.value
            state['failure_reason'] = reason
        self.stop_model(primary_name)

    def stop_model(self, primary_name: str, refresh_cache: bool = True) -> Tuple[bool, str]:
        """
        停止模型（支持中断启动中的模型）

        策略：
        1. 立即设置 STOPPED 状态（不等待启动锁，让启动线程在 Checkpoint 处自杀）。
        2. 如果有 PID，强制终止进程。
        3. 刷新监控和设备状态。
        """
        state = self.models_state[primary_name]

        logger.info(f"收到停止请求: {primary_name}")

        # 1. 设置信号
        with state['lock']:
            current_status = state['status']
            if current_status in [ModelStatus.STOPPED.value, ModelStatus.FAILED.value]:
                return True, "模型已停止"

            state['status'] = ModelStatus.STOPPED.value
            state['failure_reason'] = "被用户请求停止"
            pid = state.get('pid')

        # 2. 终止进程
        stopped_process = False
        if pid:
            proc_name = f"model_{primary_name}"
            success, msg = self.process_manager.stop_process(proc_name, force=True, timeout=3)
            if success:
                logger.info(f"进程已终止: PID {pid}")
                stopped_process = True
            else:
                logger.warning(f"进程终止异常: {msg}")

        # 3. 清理状态
        with state['lock']:
            self._reset_model_state(state)

        # 4. 后续处理
        if self.runtime_monitor.is_model_monitored(primary_name):
            self.runtime_monitor.record_model_stop(primary_name)

        if refresh_cache:
            self.plugin_manager.update_device_status()

        if stopped_process:
            return True, "模型已停止"
        else:
            return True, "已发送停止信号，正在中断启动流程..."

    def _reset_model_state(self, state: Dict[str, Any]):
        """重置状态字典为初始值"""
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
        """并行卸载所有运行中的模型"""
        logger.info("开始卸载所有模型...")
        models_to_stop = []

        # 筛选
        for name, state in self.models_state.items():
            with state['lock']:
                if state['status'] not in [ModelStatus.STOPPED.value, ModelStatus.FAILED.value]:
                    models_to_stop.append(name)

        if not models_to_stop:
            logger.info("无运行中的模型")
            return 0

        # 执行
        def stop_task(name):
            try:
                ok, msg = self.stop_model(name, refresh_cache=False)
                return name, ok, msg
            except Exception as e:
                return name, False, str(e)

        futures = [self.executor.submit(stop_task, name) for name in models_to_stop]
        stopped_count = 0
        
        timeout = len(models_to_stop) * 5 + 10
        for future in concurrent.futures.as_completed(futures, timeout=timeout):
            try:
                name, ok, msg = future.result()
                if ok:
                    stopped_count += 1
                else:
                    logger.warning(f"卸载失败 [{name}]: {msg}")
            except Exception as e:
                logger.error(f"处理结果异常: {e}")

        if stopped_count > 0:
            self.plugin_manager.update_device_status()

        logger.info(f"卸载完成: {stopped_count}/{len(models_to_stop)}")
        return stopped_count

    def idle_check_loop(self):
        """
        空闲检查守护线程
        
        规则：
        1. 模型处于 ROUTING 状态。
        2. 空闲时间超过配置阈值。
        3. 当前无待处理请求 (Pending Requests)。
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
                    # 二次确认：防止在判断间隙有新请求进入
                    if self.api_router and self.api_router.pending_requests.get(name, 0) > 0:
                        logger.info(f"模型 '{name}' 有新请求，取消自动关闭")
                        continue
                    
                    logger.info(f"触发空闲自动关闭: {name}")
                    self.stop_model(name)

            except Exception as e:
                logger.error(f"空闲检查线程异常: {e}")

    def get_all_models_status(self) -> Dict[str, Dict[str, Any]]:
        """获取所有模型的详细状态信息"""
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
                "current_script_path": adaptive.get("script_path", "") if adaptive else "配置不可用",
                "config_source": adaptive.get("config_source", "N/A") if adaptive else "N/A",
                "failure_reason": state.get('failure_reason')
            }
        return status_map

    def get_model_logs(self, primary_name: str) -> List[Dict[str, Any]]:
        try:
            return self.log_manager.get_logs(primary_name)
        except Exception as e:
            logger.error(f"获取日志异常: {e}")
            return [{"timestamp": time.time(), "message": f"System Error: {e}"}]

    def subscribe_to_model_logs(self, primary_name: str) -> queue.Queue:
        return self.log_manager.subscribe_to_logs(primary_name)

    def unsubscribe_from_model_logs(self, primary_name: str, subscriber_queue: queue.Queue):
        self.log_manager.unsubscribe_from_logs(primary_name, subscriber_queue)

    def get_log_stats(self) -> Dict[str, Any]:
        return self.log_manager.get_log_stats()

    def get_model_list(self) -> Dict[str, Any]:
        """获取符合 OpenAI API 规范的模型列表"""
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
        关闭控制器
        
        执行步骤：
        1. 停止接收新任务。
        2. 取消所有等待中的启动任务。
        3. 并行终止所有子进程。
        4. 释放所有资源。
        """
        logger.info("正在关闭模型控制器...")
        self.is_running = False
        self.shutdown_event.set()

        if self.plugin_manager:
            self.plugin_manager.stop_monitor()

        # 取消任务
        for name, future in self.startup_futures.items():
            if not future.done():
                future.cancel()

        # 并行停止所有模型
        terminated_count = self.unload_all_models()

        # 释放资源
        self.runtime_monitor.shutdown()
        self.log_manager.shutdown()
        self.executor.shutdown(wait=True)

        logger.info(f"控制器关闭完毕，清理了 {terminated_count} 个模型进程")