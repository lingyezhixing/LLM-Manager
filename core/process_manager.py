"""
Process Manager (Cross-Platform)
统一进程管理器：提供跨平台(Windows/Linux)的进程启动、监控、输出捕获及防孤儿进程的终止功能。
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
    STOPPED = "stopped"     # 已停止
    RUNNING = "running"     # 运行中
    STARTING = "starting"   # 启动中
    STOPPING = "stopping"   # 停止中
    FAILED = "failed"       # 启动失败或异常退出


@dataclass
class ProcessInfo:
    """进程元数据结构"""
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
    """
    跨平台统一进程管理器
    
    主要特性：
    1. 线程安全的操作
    2. 跨平台进程树查杀（防止僵尸/孤儿进程）
    3. 异步输出流捕获
    """

    def __init__(self):
        self.processes: Dict[str, ProcessInfo] = {}
        # 使用 RLock 允许同一线程重入（例如 stop_all 调用 stop_process）
        self.lock = threading.RLock()
        self.monitor_thread = None
        self.is_monitoring = True
        self.shutdown_event = threading.Event()
        self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=10)
        self._process_cleanup_complete = threading.Event()

        # 启动后台监控线程
        self.monitor_thread = threading.Thread(target=self._monitor_processes, daemon=True)
        self.monitor_thread.start()

        logger.info(f"进程管理器已初始化 (操作系统: {os.name})")

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
        启动新进程
        
        Args:
            name: 唯一进程标识名
            command: 执行命令
            cwd: 工作目录
            description: 描述信息
            shell: 是否通过 shell 执行
            creation_flags: Windows 特有创建标志
            capture_output: 是否捕获 stdout/stderr
            output_callback: 输出回调函数
            
        Returns:
            (success, message, pid)
        """
        with self.lock:
            # 检查重名进程
            if name in self.processes:
                existing = self.processes[name]
                if existing.status == ProcessStatus.RUNNING:
                    return False, f"启动取消: 进程 '{name}' 已在运行", existing.pid
                elif existing.status == ProcessStatus.STARTING:
                    return False, f"启动取消: 进程 '{name}' 正在启动中", existing.pid

            # 初始化进程信息占位
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
            logger.info(f"正在启动进程: {name} (Cmd: {command[:50]}...)")

            # 配置基础启动参数
            startup_params = {
                'shell': shell,
                'text': True,
                'encoding': 'utf-8',
                'errors': 'replace'
            }

            if cwd:
                startup_params['cwd'] = cwd

            # --- 平台特定配置：确保能查杀整个进程树 ---
            if os.name == 'nt':
                # Windows: 使用 CREATE_NEW_PROCESS_GROUP 以便 taskkill /T 生效
                if creation_flags is None:
                    startup_params['creationflags'] = subprocess.CREATE_NEW_PROCESS_GROUP
                else:
                    startup_params['creationflags'] = creation_flags
            else:
                # Linux: 使用 start_new_session=True (执行 setsid)
                # 这使得子进程成为新的会话组长，PID == PGID，便于 killpg 一次性查杀
                startup_params['start_new_session'] = True
                startup_params['close_fds'] = True

            # 输出流配置
            if capture_output:
                startup_params.update({
                    'stdout': subprocess.PIPE,
                    'stderr': subprocess.PIPE,
                    'bufsize': 1  # 行缓冲模式
                })

            # 执行系统调用
            process = subprocess.Popen(command, **startup_params)

            # 更新进程状态
            with self.lock:
                process_info.pid = process.pid
                process_info.process = process
                process_info.start_time = time.time()
                process_info.status = ProcessStatus.RUNNING

            # 启动输出流监控线程
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

            logger.error(f"进程启动异常: {name} - {str(e)}")
            return False, f"启动进程失败: {e}", None

    def _monitor_output(self, process: subprocess.Popen, process_info: ProcessInfo, stream_type: str, callback: Callable[[str, str], None]):
        """后台线程：实时捕获并转发进程输出"""
        stream = getattr(process, stream_type)
        try:
            while True:
                line = stream.readline()
                if not line:
                    break
                line = line.rstrip('\n\r')
                if line.strip():  # 仅回调有效内容
                    callback(stream_type, line)
        except ValueError:
            # 文件描述符可能已关闭
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
        停止指定进程
        
        Args:
            name: 进程名
            force: 是否强制终止（查杀进程树）
            timeout: 优雅退出的等待超时时间
        """
        with self.lock:
            if name not in self.processes:
                return True, f"操作忽略: 进程 '{name}' 不存在"

            process_info = self.processes[name]

            # 状态检查：防止重复停止
            if process_info.status == ProcessStatus.STOPPED:
                return True, f"操作忽略: 进程 '{name}' 已停止"

            # 如果正在停止中且非强制，则跳过
            if process_info.status == ProcessStatus.STOPPING and not force:
                return True, f"等待中: 进程 '{name}' 正在停止"

            # 标记为停止中
            process_info.status = ProcessStatus.STOPPING

        try:
            pid = process_info.pid
            
            # 处理初始化阶段 PID 尚未生成的特殊情况
            if pid == 0:
                with self.lock:
                    process_info.status = ProcessStatus.STOPPED
                    process_info.stop_time = time.time()
                return True, "进程未完全启动，状态已重置"

            logger.info(f"请求停止进程: {name} (PID: {pid}, Force: {force})")
            success = False
            message = ""

            if force:
                success = self._kill_process_tree(pid)
                message = "进程树已强制终止" if success else "强制终止失败"
            else:
                success = self._terminate_process(pid, timeout)
                message = "进程已正常关闭" if success else "正常关闭失败"

            # 更新最终状态
            with self.lock:
                process_info.status = ProcessStatus.STOPPED
                process_info.stop_time = time.time()
                process_info.process = None

            return success, message

        except Exception as e:
            logger.error(f"停止进程异常: {name} - {e}")
            return False, f"停止进程失败: {e}"

    def _terminate_process(self, pid: int, timeout: int) -> bool:
        """尝试优雅终止进程 (SIGTERM/Terminate)，超时后自动升级为强制查杀"""
        try:
            process = psutil.Process(pid)
            process.terminate()

            try:
                process.wait(timeout=min(timeout, 5))
                return True
            except psutil.TimeoutExpired:
                logger.warning(f"进程 {pid} 响应超时，正在升级为强制查杀...")
                return self._kill_process_tree(pid)

        except psutil.NoSuchProcess:
            return True
        except Exception as e:
            logger.error(f"终止进程 {pid} 出错: {e}")
            return False

    def _kill_process_tree(self, pid: int) -> bool:
        """
        强制终止进程树 (Cross-Platform)
        策略：
        1. 优先使用 psutil 递归查杀。
        2. 失败时回退到系统命令 (taskkill / killpg)。
        """
        
        # 策略 1: psutil (优雅且跨平台)
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
            
            # 等待清理
            _, alive = psutil.wait_procs([parent] + children, timeout=3)
            if not alive:
                return True
        except psutil.NoSuchProcess:
            return True  # 进程已不存在
        except Exception as e:
            logger.warning(f"psutil 查杀不完整: {e}，尝试系统命令兜底")

        # 策略 2: 系统原生命令兜底
        if os.name == 'nt':
            # Windows: taskkill /T (Tree)
            try:
                result = subprocess.run(
                    ['taskkill', '/F', '/T', '/PID', str(pid)],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    timeout=5
                )
                return result.returncode in [0, 128] # 0=成功, 128=未找到(视为成功)
            except Exception:
                return False
        else:
            # Linux: killpg (基于 PGID)
            try:
                # 对应启动时的 start_new_session=True，此时 PID 即 PGID
                os.killpg(pid, signal.SIGKILL)
                return True
            except ProcessLookupError:
                return True 
            except Exception:
                # 最后的尝试：pkill 子进程 + kill 父进程
                try:
                    subprocess.run(['pkill', '-9', '-P', str(pid)], 
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    os.kill(pid, signal.SIGKILL)
                    return True
                except Exception as e:
                    logger.error(f"Linux 强制查杀失败: {e}")
                    return False

    def _is_process_alive(self, pid: int) -> bool:
        """检查进程是否存活且非僵尸状态"""
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
        """
        守护线程：定期检查进程存活状态
        自动更新已退出的进程状态，并回收资源
        """
        while self.is_monitoring:
            try:
                if self.shutdown_event.is_set():
                    break

                time.sleep(5)  # 轮询间隔

                with self.lock:
                    # 使用 list() 复制，避免遍历时修改字典
                    for name, process_info in list(self.processes.items()):
                        # 仅监控被标记为运行中的进程
                        if process_info.status in [ProcessStatus.RUNNING, ProcessStatus.STARTING]:
                            if process_info.pid == 0:
                                continue

                            if not self._is_process_alive(process_info.pid):
                                logger.info(f"监测到进程退出: {name} (PID: {process_info.pid})")
                                process_info.status = ProcessStatus.STOPPED
                                process_info.stop_time = time.time()
                                process_info.process = None

                                # 尝试获取退出码
                                try:
                                    if process_info.process:
                                        process_info.exit_code = process_info.process.poll()
                                except Exception:
                                    pass

                    # 清理过期记录
                    self._cleanup_old_records()

            except Exception as e:
                logger.error(f"进程监控线程异常: {e}")

    def _cleanup_old_records(self):
        """清理已停止且过期的进程记录，防止内存无限增长"""
        stopped_processes = [(name, p) for name, p in self.processes.items()
                           if p.status == ProcessStatus.STOPPED]
        
        # 保持最近 50 条历史记录
        if len(stopped_processes) > 50:
            stopped_processes.sort(key=lambda x: x[1].stop_time or 0)
            for name, _ in stopped_processes[:len(stopped_processes) - 50]:
                del self.processes[name]

    def get_process_info(self, name: str) -> Optional[Dict[str, Any]]:
        """
        获取单个进程的详细信息
        包含惰性状态检查逻辑
        """
        with self.lock:
            if name not in self.processes:
                return None

            p = self.processes[name]
            
            # 惰性检查：如果查的时候发现已经死了，立刻更新状态
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
        """获取所有受管进程列表"""
        with self.lock:
            return [info for name in self.processes if (info := self.get_process_info(name))]

    def stop_all_processes(self, force: bool = False) -> Dict[str, Tuple[bool, str]]:
        """
        并行停止所有受管进程
        
        Args:
            force: 是否强制查杀
        """
        results = {}
        with self.lock:
            # 筛选出需要停止的进程
            target_names = [
                name for name, p in self.processes.items() 
                if p.status in [ProcessStatus.RUNNING, ProcessStatus.STARTING, ProcessStatus.STOPPING]
            ]

        if not target_names:
            self._process_cleanup_complete.set()
            return results

        logger.info(f"开始批量停止 {len(target_names)} 个进程 (Force={force})...")

        def stop_task(name):
            # 强制模式下缩短超时时间
            timeout = 3 if force else 10
            return name, self.stop_process(name, force=force, timeout=timeout)

        future_to_name = {
            self.executor.submit(stop_task, name): name 
            for name in target_names
        }

        try:
            for future in concurrent.futures.as_completed(future_to_name, timeout=30):
                name, res = future.result()
                results[name] = res
        except concurrent.futures.TimeoutError:
            logger.error("批量停止进程操作超时")

        self._process_cleanup_complete.set()
        return results

    def cleanup(self):
        """资源回收：关闭管理器并强制终止所有进程"""
        logger.info("正在执行进程管理器清理程序...")
        self.is_monitoring = False
        self.shutdown_event.set()

        # 强制停止所有子进程
        self.stop_all_processes(force=True)

        self.executor.shutdown(wait=False)
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=1)

        logger.info("进程管理器清理完毕")


# --- 全局单例接口 ---

_global_process_manager = None

def get_process_manager() -> ProcessManager:
    """获取 ProcessManager 全局单例"""
    global _global_process_manager
    if _global_process_manager is None:
        _global_process_manager = ProcessManager()
    return _global_process_manager

def cleanup_process_manager():
    """销毁 ProcessManager 全局单例"""
    global _global_process_manager
    if _global_process_manager:
        _global_process_manager.cleanup()
        _global_process_manager = None