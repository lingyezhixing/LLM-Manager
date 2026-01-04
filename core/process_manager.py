"""
统一的进程管理器 (Windows/Linux 兼容版 - 增强版)
提供进程启动、追踪、输出捕获和彻底的跨平台终止功能 (防孤儿进程)
"""

import subprocess
import time
import threading
import psutil
import concurrent.futures
import os
import signal
from typing import Dict, Optional, Tuple, List, Any, Callable
from dataclasses import dataclass
from enum import Enum
from utils.logger import get_logger

logger = get_logger(__name__)


class ProcessStatus(Enum):
    """进程状态枚举"""
    STOPPED = "stopped"
    RUNNING = "running"
    STARTING = "starting"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass
class ProcessInfo:
    """进程信息数据类"""
    pid: int
    name: str
    status: ProcessStatus
    process: Optional[subprocess.Popen] = None
    start_time: Optional[float] = None
    stop_time: Optional[float] = None
    exit_code: Optional[int] = None
    command: Optional[str] = None
    description: Optional[str] = None
    output_callback: Optional[Callable[[str, str], None]] = None
    stdout_thread: Optional[threading.Thread] = None
    stderr_thread: Optional[threading.Thread] = None


class ProcessManager:
    """跨平台统一进程管理器"""

    def __init__(self):
        """初始化进程管理器"""
        self.processes: Dict[str, ProcessInfo] = {}
        # 使用 RLock 允许同一线程重入（例如 stop_all 调用 stop_process）
        self.lock = threading.RLock()
        self.monitor_thread = None
        self.is_monitoring = True
        self.shutdown_event = threading.Event()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
        self._process_cleanup_complete = threading.Event()

        # 启动监控线程
        self.monitor_thread = threading.Thread(target=self._monitor_processes, daemon=True)
        self.monitor_thread.start()

        logger.info(f"进程管理器初始化完成 (OS: {os.name})")

    def start_process(
        self,
        name: str,
        command: str,
        cwd: Optional[str] = None,
        description: Optional[str] = None,
        shell: bool = True,
        creation_flags: Optional[int] = None,
        capture_output: bool = False,
        output_callback: Optional[Callable[[str, str], None]] = None
    ) -> Tuple[bool, str, Optional[int]]:
        """
        启动进程 (跨平台适配 + 防孤儿进程)
        """
        with self.lock:
            # 检查是否已存在同名进程
            if name in self.processes:
                existing = self.processes[name]
                if existing.status == ProcessStatus.RUNNING:
                    return False, f"进程 '{name}' 已在运行", existing.pid
                elif existing.status == ProcessStatus.STARTING:
                    return False, f"进程 '{name}' 正在启动中", existing.pid

            # 创建初始进程信息
            process_info = ProcessInfo(
                pid=0,
                name=name,
                status=ProcessStatus.STARTING,
                command=command,
                description=description,
                output_callback=output_callback
            )

            self.processes[name] = process_info

        try:
            logger.info(f"正在启动进程: {name}")

            # 基础参数
            startup_params = {
                'shell': shell,
                'text': True,
                'encoding': 'utf-8',
                'errors': 'replace'
            }

            if cwd:
                startup_params['cwd'] = cwd

            # --- 关键修复：跨平台孤儿进程防护 ---
            if os.name == 'nt':
                # Windows: 使用 CREATE_NEW_PROCESS_GROUP
                # 这允许 taskkill /T 准确识别整个进程树
                if creation_flags is None:
                    startup_params['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
                else:
                    startup_params['creationflags'] = creation_flags
            else:
                # Linux: 使用 start_new_session=True (Python 3.2+)
                # 这会执行 setsid()，使子进程成为新的会话首领和进程组组长
                # 这样 PID 就等于 PGID (进程组ID)，方便 os.killpg() 一锅端
                startup_params['start_new_session'] = True
                # 关闭不需要的文件描述符
                startup_params['close_fds'] = True
            # -----------------------------------

            if capture_output:
                startup_params.update({
                    'stdout': subprocess.PIPE,
                    'stderr': subprocess.PIPE,
                    'bufsize': 1  # 行缓冲
                })

            # 启动进程
            process = subprocess.Popen(command, **startup_params)

            # 更新进程信息
            with self.lock:
                process_info.pid = process.pid
                process_info.process = process
                process_info.start_time = time.time()
                process_info.status = ProcessStatus.RUNNING

            # 启动输出监控线程
            if capture_output and output_callback:
                process_info.stdout_thread = threading.Thread(
                    target=self._monitor_output,
                    args=(process, process_info, 'stdout', output_callback),
                    daemon=True
                )
                process_info.stderr_thread = threading.Thread(
                    target=self._monitor_output,
                    args=(process, process_info, 'stderr', output_callback),
                    daemon=True
                )
                process_info.stdout_thread.start()
                process_info.stderr_thread.start()

            logger.info(f"进程启动成功: {name} (PID: {process.pid})")
            return True, "进程启动成功", process.pid

        except Exception as e:
            with self.lock:
                process_info.status = ProcessStatus.FAILED
                process_info.stop_time = time.time()

            logger.error(f"启动进程失败: {name} - {e}")
            return False, f"启动进程失败: {e}", None

    def _monitor_output(self, process: subprocess.Popen, process_info: ProcessInfo, stream_type: str, callback: Callable[[str, str], None]):
        """监控进程输出流 (健壮性优化版)"""
        stream = getattr(process, stream_type)
        try:
            # 持续读取直到流关闭
            while True:
                line = stream.readline()
                if not line:
                    break
                line = line.rstrip('\n\r')
                if line.strip():  # 只回调非空行
                    callback(stream_type, line)
        except ValueError:
            # 文件已关闭
            pass
        except Exception:
            pass
        finally:
            try:
                stream.close()
            except:
                pass

    def stop_process(
        self,
        name: str,
        force: bool = False,
        timeout: int = 10
    ) -> Tuple[bool, str]:
        """
        停止进程 (支持强制查杀进程树)
        """
        with self.lock:
            if name not in self.processes:
                return True, f"进程 '{name}' 不存在"

            process_info = self.processes[name]

            # 即使状态是 STARTING，如果 force=True 也允许停止（防止启动中被卡死）
            if process_info.status == ProcessStatus.STOPPED:
                return True, f"进程 '{name}' 已停止"

            if process_info.status == ProcessStatus.STOPPING and not force:
                return True, f"进程 '{name}' 正在停止中"

            process_info.status = ProcessStatus.STOPPING

        try:
            pid = process_info.pid
            
            # 处理进程刚刚启动，PID 可能尚未生成的情况
            if pid == 0:
                with self.lock:
                    process_info.status = ProcessStatus.STOPPED
                    process_info.stop_time = time.time()
                return True, "进程尚未完全启动，状态已重置"

            logger.info(f"正在停止进程: {name} (PID: {pid}, 强制: {force})")
            success = False
            message = ""

            if force:
                # 强制终止进程树
                success = self._kill_process_tree(pid)
                message = "进程已强制终止" if success else "强制终止失败"
            else:
                # 正常关闭
                success = self._terminate_process(pid, timeout)
                message = "进程已正常关闭" if success else "正常关闭失败"

            # 更新最终状态
            with self.lock:
                process_info.status = ProcessStatus.STOPPED
                process_info.stop_time = time.time()
                process_info.process = None
                # 线程引用由 daemon 自动回收

            return success, message

        except Exception as e:
            logger.error(f"停止进程失败: {name} - {e}")
            return False, f"停止进程失败: {e}"

    def _terminate_process(self, pid: int, timeout: int) -> bool:
        """优化的正常终止进程"""
        try:
            process = psutil.Process(pid)
            process.terminate()  # Windows: TerminateProcess, Linux: SIGTERM

            try:
                # 等待进程退出
                process.wait(timeout=min(timeout, 5))
                return True
            except psutil.TimeoutExpired:
                logger.warning(f"进程 {pid} 响应超时，升级为强制查杀")
                return self._kill_process_tree(pid)

        except psutil.NoSuchProcess:
            return True
        except Exception as e:
            logger.error(f"终止进程 {pid} 异常: {e}")
            return False

    def _kill_process_tree(self, pid: int) -> bool:
        """强制终止进程树 (核心修复：Linux使用killpg，Windows使用taskkill /T)"""
        
        # 1. 优先尝试 psutil (最优雅的方式)
        try:
            parent = psutil.Process(pid)
            children = parent.children(recursive=True)
            
            # 先杀子进程
            for child in children:
                try:
                    child.kill()
                except psutil.NoSuchProcess:
                    pass
            
            # 再杀父进程
            parent.kill()
            
            # 确认死亡
            _, alive = psutil.wait_procs([parent] + children, timeout=3)
            if not alive:
                return True
        except psutil.NoSuchProcess:
            return True  # 已经不存在了
        except Exception as e:
            logger.warning(f"psutil 查杀不完整: {e}，尝试系统命令兜底")

        # 2. 系统命令兜底 (Fallback)
        if os.name == 'nt':
            # Windows: 使用 taskkill /T 终止进程树
            try:
                result = subprocess.run(
                    ['taskkill', '/F', '/T', '/PID', str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5
                )
                return result.returncode in [0, 128] # 0=成功, 128=未找到(已死)
            except Exception:
                return False
        else:
            # Linux: 使用 killpg (对应 start_new_session=True)
            try:
                # 因为启动时设置了 session/group leader，pid 就是 pgid
                os.killpg(pid, signal.SIGKILL)
                return True
            except ProcessLookupError:
                return True # 进程组已不存在
            except Exception:
                # 最后的手段：尝试 pkill 子进程 + kill 父进程
                try:
                    subprocess.run(['pkill', '-9', '-P', str(pid)], 
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    os.kill(pid, signal.SIGKILL)
                    return True
                except Exception as e:
                    logger.error(f"Linux 强制查杀失败: {e}")
                    return False

    def _is_process_alive(self, pid: int) -> bool:
        """检查进程是否存活"""
        if pid == 0: 
            return False
        try:
            process = psutil.Process(pid)
            if process.status() == psutil.STATUS_ZOMBIE:
                return False
            return process.is_running()
        except psutil.NoSuchProcess:
            return False
        except Exception:
            return False

    def _monitor_processes(self):
        """优化的进程监控线程"""
        while self.is_monitoring:
            try:
                if self.shutdown_event.is_set():
                    break

                time.sleep(5)  # 5秒检查一次

                with self.lock:
                    # 使用 list() 复制以避免迭代时修改
                    for name, process_info in list(self.processes.items()):
                        if process_info.status in [ProcessStatus.RUNNING, ProcessStatus.STARTING]:
                            # 跳过刚创建还没 PID 的
                            if process_info.pid == 0:
                                continue

                            if not self._is_process_alive(process_info.pid):
                                logger.info(f"检测到进程已退出: {name} (PID: {process_info.pid})")
                                process_info.status = ProcessStatus.STOPPED
                                process_info.stop_time = time.time()
                                process_info.process = None

                                # 尝试获取退出码
                                try:
                                    if process_info.process:
                                        process_info.exit_code = process_info.process.poll()
                                except Exception:
                                    pass

                    # 清理过期的已停止记录 (保持内存整洁)
                    self._cleanup_old_records()

            except Exception as e:
                logger.error(f"进程监控线程出错: {e}")

    def _cleanup_old_records(self):
        """清理过旧的进程记录"""
        stopped_processes = [(name, p) for name, p in self.processes.items()
                           if p.status == ProcessStatus.STOPPED]
        
        if len(stopped_processes) > 50:
            # 按停止时间排序，删除最旧的
            stopped_processes.sort(key=lambda x: x[1].stop_time or 0)
            for name, _ in stopped_processes[:len(stopped_processes) - 50]:
                del self.processes[name]

    def get_process_info(self, name: str) -> Optional[Dict[str, Any]]:
        """获取进程详细信息"""
        with self.lock:
            if name not in self.processes:
                return None

            p = self.processes[name]
            
            # 实时状态更新 (惰性检查)
            if p.status == ProcessStatus.RUNNING and p.pid > 0:
                if not self._is_process_alive(p.pid):
                    p.status = ProcessStatus.STOPPED
                    p.stop_time = time.time()

            return {
                'name': p.name,
                'pid': p.pid,
                'status': p.status.value,
                'start_time': p.start_time,
                'uptime': (time.time() - p.start_time) if p.start_time and p.status == ProcessStatus.RUNNING else 0
            }

    def list_processes(self) -> List[Dict[str, Any]]:
        """列出所有进程"""
        with self.lock:
            # 过滤掉 None，确保列表安全
            return [info for name in self.processes if (info := self.get_process_info(name))]

    def stop_all_processes(self, force: bool = False) -> Dict[str, Tuple[bool, str]]:
        """并行停止所有进程"""
        results = {}
        with self.lock:
            # 只停止没停的
            target_names = [
                name for name, p in self.processes.items() 
                if p.status in [ProcessStatus.RUNNING, ProcessStatus.STARTING, ProcessStatus.STOPPING]
            ]

        if not target_names:
            self._process_cleanup_complete.set()
            return results

        logger.info(f"并行停止 {len(target_names)} 个进程...")

        def stop_task(name):
            # 强制模式超时给短点，正常模式多给点时间
            timeout = 3 if force else 10
            return name, self.stop_process(name, force=force, timeout=timeout)

        future_to_name = {
            self.executor.submit(stop_task, name): name 
            for name in target_names
        }

        # 等待所有任务
        try:
            for future in concurrent.futures.as_completed(future_to_name, timeout=30):
                name, res = future.result()
                results[name] = res
        except concurrent.futures.TimeoutError:
            logger.error("批量停止进程超时")

        self._process_cleanup_complete.set()
        return results

    def cleanup(self):
        """完全清理并关闭管理器"""
        logger.info("正在清理进程管理器...")
        self.is_monitoring = False
        self.shutdown_event.set()

        # 强制停止所有
        self.stop_all_processes(force=True)

        self.executor.shutdown(wait=False)
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=1)

        logger.info("进程管理器清理完成")


# 全局单例模式
_global_process_manager = None

def get_process_manager() -> ProcessManager:
    global _global_process_manager
    if _global_process_manager is None:
        _global_process_manager = ProcessManager()
    return _global_process_manager

def cleanup_process_manager():
    global _global_process_manager
    if _global_process_manager:
        _global_process_manager.cleanup()
        _global_process_manager = None