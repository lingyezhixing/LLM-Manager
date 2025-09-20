import json
import subprocess
import time
import threading
import logging
import os
import openai # 替换 httpx 和 asyncio
from gpu_utils import get_available_vram, get_gpu_info

logging.basicConfig(level=logging.INFO, format='%(asctime)s - [%(levelname)s] - %(message)s')
logger = logging.getLogger(__name__)

class ModelManager:
    def __init__(self, config_path='config.json'):
        self.config_path = config_path
        self.gpus = get_gpu_info()
        self.gpu_map = {gpu.simple_name: gpu for gpu in self.gpus} if self.gpus else {}
        self.models_state = {}
        self.alias_to_primary_name = {}
        self.config_lock = threading.Lock()
        # 新增：全局模型加载锁，用于确保一次只加载一个模型
        self.loading_lock = threading.Lock()
        self.load_config()
        self.is_running = True
        self.idle_check_thread = threading.Thread(target=self.idle_check_loop, daemon=True)
        self.idle_check_thread.start()

    def load_config(self):
        with self.config_lock:
            try:
                with open(self.config_path, 'r', encoding='utf-8') as f:
                    self.config = json.load(f)
                self._init_states()
            except (ValueError, FileNotFoundError) as e:
                logger.error(f"加载或解析配置文件 '{self.config_path}' 失败: {e}")
                raise

    def _init_states(self):
        new_states = {}
        self.alias_to_primary_name.clear()
        all_aliases_check = set()
        if "program" not in self.config:
            raise ValueError("配置文件中缺少 'program' 部分。")
        for key, model_cfg in self.config.items():
            if key == "program": continue
            aliases = model_cfg.get("aliases")
            if not isinstance(aliases, list) or not aliases:
                raise ValueError(f"模型配置 '{key}' 缺少 'aliases' 或其为空列表。")
            primary_name = aliases[0]
            for alias in aliases:
                if alias in all_aliases_check:
                    raise ValueError(f"配置错误: 别名 '{alias}' 重复。")
                all_aliases_check.add(alias)
                self.alias_to_primary_name[alias] = primary_name
            
            if primary_name in self.models_state:
                new_states[primary_name] = self.models_state[primary_name]
            else:
                new_states[primary_name] = {
                    "process": None, "status": "stopped",
                    "last_access": None, "pid": None,
                    "pending_requests": 0, "lock": threading.Lock(),
                    "output_log": [], "log_thread": None
                }
        self.models_state = new_states

    def _resolve_primary_name(self, alias: str) -> str:
        primary_name = self.alias_to_primary_name.get(alias)
        if not primary_name:
            raise KeyError(f"无法解析模型别名 '{alias}'。")
        return primary_name

    def get_model_config(self, alias: str):
        primary_name = self.alias_to_primary_name.get(alias)
        if not primary_name: return None
        for key, model_cfg in self.config.items():
            if key != "program" and model_cfg.get("aliases", []) and model_cfg["aliases"][0] == primary_name:
                return model_cfg
        return None
    
    def get_adaptive_model_config(self, alias: str):
        """
        根据当前GPU状态获取自适应模型配置
        按照配置文件中的优先级顺序尝试不同的配置方案
        """
        base_config = self.get_model_config(alias)
        if not base_config:
            return None
            
        # 获取当前在线的GPU
        current_gpus = {gpu.simple_name for gpu in self.gpus}
        
        # 重新获取最新的GPU信息
        self.gpus = get_gpu_info()
        current_gpus = {gpu.simple_name for gpu in self.gpus}
        logger.info(f"模型 '{alias}' 启动时检测到GPU: {current_gpus}")
        
        # 按优先级顺序尝试不同的配置方案
        # 首先查找所有以GPU组合名称开头的配置键
        priority_configs = []
        for key in base_config.keys():
            if key not in ["aliases", "bat_path", "mode", "gpu_mem_mb", "port", "auto_start", "alternate_config"]:
                config_data = base_config[key]
                if isinstance(config_data, dict) and "required_gpus" in config_data:
                    priority_configs.append((key, config_data))
        
        # 按照配置文件中的顺序（即用户定义的优先级）进行尝试
        for config_name, config_data in priority_configs:
            required_gpus = set(config_data.get("required_gpus", []))
            
            if required_gpus.issubset(current_gpus):
                logger.info(f"模型 '{alias}' 使用配置方案: {config_name}，需要GPU: {required_gpus}")
                
                # 构建完整的自适应配置
                adaptive_config = base_config.copy()
                
                # 移除旧的配置键
                for key in list(adaptive_config.keys()):
                    if key not in ["aliases", "mode", "port", "auto_start"]:
                        if key.startswith("RTX") or key in ["alternate_config"]:
                            del adaptive_config[key]
                
                # 添加新的配置值
                adaptive_config.update({
                    "bat_path": config_data["bat_path"],
                    "gpu_mem_mb": config_data["gpu_mem_mb"],
                    "config_source": config_name  # 记录使用的配置来源
                })
                
                return adaptive_config
        
        # 如果没有找到合适的配置，返回None表示不可用
        logger.warning(f"模型 '{alias}' 没有找到适合当前GPU状态 {current_gpus} 的配置方案")
        return None
        
    def _log_process_output(self, stream, log_list):
        try:
            for line in iter(stream.readline, ''):
                log_list.append(line.strip())
                if len(log_list) > 200: # 保持最近200行日志
                    log_list.pop(0)
        finally:
            stream.close()

    def _perform_deep_health_check(self, alias: str, model_config: dict, start_time: float, timeout_seconds: int):
        primary_name = self._resolve_primary_name(alias)
        port = model_config['port']
        model_mode = model_config.get('mode', 'Chat')

        probe_path_display = ""
        if model_mode == "Chat":
            probe_path_display = "/v1/chat/completions"
        elif model_mode == "Base":
            probe_path_display = "/v1/completions"
        elif model_mode == "Embedding":
            probe_path_display = "/v1/embeddings"
        elif model_mode == "Reranker":
            probe_path_display = "/v1/rerank"
        else:
            logger.info(f"模型 '{primary_name}' ({model_mode} 模式) 无需深度健康检查，跳过。")
            return True, "无需深度健康检查"

        logger.info(f"阶段 2/2: 正在对模型 '{primary_name}' 进行深度健康检查 (端点: {probe_path_display})...")

        while time.time() - start_time < timeout_seconds:
            try:
                # 使用 openai 库进行同步健康检查
                try:
                    client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
                except Exception as e:
                    logger.error(f"创建 OpenAI 客户端失败: {e}")
                    return False, f"创建 OpenAI 客户端失败: {e}"
                if model_mode == "Chat":
                    client.chat.completions.create(
                        model=alias, messages=[{"role": "user", "content": "hello"}], max_tokens=1, stream=False, timeout=5.0
                    )
                elif model_mode == "Base":
                    client.completions.create(
                        model=alias, prompt="hello", max_tokens=1, stream=False, timeout=5.0
                    )
                elif model_mode == "Embedding":
                    client.embeddings.create(
                        model=alias, input="hello", encoding_format="float", timeout=5.0
                    )
                elif model_mode == "Reranker":
                    response = client._client.post(
                        "rerank",
                        json={
                            "model": alias,
                            "query": "hello",
                            "documents": ["hello world", "test document"],
                            "top_n": 1
                        },
                        timeout=5.0
                    )
                    response.raise_for_status()
                
                logger.info(f"模型 '{primary_name}' 深度健康检查通过！")
                return True, "深度健康检查成功"
            
            except openai.APIConnectionError as e:
                logger.debug(f"模型 '{primary_name}' API 连接错误: {e.__cause__}")
            except openai.APIStatusError as e:
                 logger.debug(f"模型 '{primary_name}' 返回非成功状态码: {e.status_code} - {e.response}")
            except openai.APITimeoutError:
                logger.debug(f"模型 '{primary_name}' 深度健康检查请求超时。")
            except Exception as e:
                logger.warning(f"深度健康检查期间出现意外错误: {e}")
            
            time.sleep(1)
        
        logger.error(f"超时: 模型 '{primary_name}' 未在规定时间内通过深度健康检查。")
        return False, "深度健康检查超时"

    def start_model(self, alias: str, bypass_vram_check: bool = False):
        primary_name = self._resolve_primary_name(alias)
        state = self.models_state[primary_name]

        # 快速检查，避免不必要的全局锁等待
        with state['lock']:
            if state['status'] == "running":
                return True, f"模型 '{primary_name}' 已在运行。"
            elif state['status'] == "starting":
                # 模型正在启动，等待启动完成
                pass  # 继续执行获取全局加载锁的逻辑

        # --- 修改核心逻辑：使用全局加载锁 ---
        logger.info(f"请求加载模型 '{primary_name}'，正在等待全局加载锁...")
        with self.loading_lock:
            logger.info(f"已获得 '{primary_name}' 的全局加载锁，开始加载流程。")
            
            # 双重检查：在等待锁之后，再次确认模型状态
            with state['lock']:
                if state['status'] == "running":
                    logger.info(f"模型 '{primary_name}' 在等待期间已被其他请求加载，跳过启动。")
                    return True, f"模型 {primary_name} 已成功启动。"
                elif state['status'] == "starting":
                    # 模型正在启动，等待启动完成
                    logger.info(f"模型 '{primary_name}' 正在启动中，等待完成...")
                    # 等待启动完成，最多等待5分钟
                    wait_start = time.time()
                    max_wait_time = 300
                    while state['status'] == "starting":
                        if time.time() - wait_start > max_wait_time:
                            state['lock'].release()  # 临时释放锁以允许其他操作
                            logger.error(f"等待模型 '{primary_name}' 启动超时")
                            return False, f"等待模型 '{primary_name}' 启动超时"
                        # 释放锁让其他线程可以修改状态，然后重新获取
                        state['lock'].release()
                        time.sleep(0.5)
                        state['lock'].acquire()

                    # 重新检查状态
                    if state['status'] == "running":
                        logger.info(f"模型 '{primary_name}' 启动完成。")
                        return True, f"模型 {primary_name} 已成功启动。"
                    else:
                        logger.error(f"模型 '{primary_name}' 启动失败。")
                        return False, f"模型 {primary_name} 启动失败。"

                # 确认由当前线程执行加载
                state['status'] = "starting"
                state['output_log'].clear()
                state['output_log'].append(f"--- {time.ctime()} ---")

            # --- 以下是原有的加载逻辑，现在被全局锁保护 ---
            try:
                # 使用自适应配置
                model_config = self.get_adaptive_model_config(alias)
                if not model_config:
                    state['status'] = "stopped"
                    current_gpus = {gpu.simple_name for gpu in self.gpus}
                    error_msg = f"启动 '{primary_name}' 失败：没有适合当前GPU状态 {current_gpus} 的配置方案。"
                    logger.error(error_msg)
                    state['output_log'].append(f"[ERROR] {error_msg}")
                    return False, error_msg
                
                global_disable_gpu_mon = self.config['program'].get('Disable_GPU_monitoring', False)
                if not global_disable_gpu_mon and not bypass_vram_check:
                    logger.info(f"正在为模型 '{primary_name}' 检查显存...")
                    if not self._check_and_free_vram(model_config):
                        state['status'] = "stopped"
                        error_msg = f"启动 '{primary_name}' 失败：显存不足。"
                        logger.error(error_msg)
                        state['output_log'].append(f"[ERROR] {error_msg}")
                        return False, error_msg
                else:
                    logger.warning(f"GPU显存检查被禁用或绕过，将直接启动模型 '{primary_name}'。")
                    state['output_log'].append("[WARNING] GPU显存检查被跳过。")

                logger.info(f"正在启动模型: {primary_name} (由别名 '{alias}' 触发)...")
                config_source = model_config.get('config_source', '默认')
                logger.info(f"使用配置方案: {config_source}")
                logger.info(f"启动脚本: {model_config['bat_path']}")
                logger.info(f"GPU需求: {model_config.get('gpu_mem_mb', {})}")
                bat_path = model_config['bat_path']
                
                project_root = os.path.dirname(os.path.abspath(self.config_path))
                process = subprocess.Popen(
                    bat_path, shell=True, creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
                    cwd=project_root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding='utf-8', errors='replace', bufsize=1
                )
                log_thread = threading.Thread(
                    target=self._log_process_output, args=(process.stdout, state['output_log']), daemon=True
                )
                log_thread.start()
                state.update({"process": process, "pid": process.pid, "log_thread": log_thread})

                port = model_config['port']
                timeout_seconds = 300
                start_time = time.time()
                
                logger.info(f"阶段 1/2: 等待模型 '{primary_name}' API 服务上线 (端点: /v1/models)...")
                initial_check_passed = False

                while time.time() - start_time < timeout_seconds:
                    if process.poll() is not None:
                        msg = f"模型 '{primary_name}' 进程在启动期间意外终止。"
                        logger.error(msg)
                        self.mark_model_as_stopped(alias, acquire_lock=False)
                        return False, msg
                    try:
                        client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
                        client.models.list(timeout=3.0)
                        logger.info(f"阶段 1/2: 模型 '{primary_name}' API 服务已上线。")
                        initial_check_passed = True
                        break
                    except (openai.APIConnectionError, openai.APITimeoutError):
                        time.sleep(2) # 等待服务启动
                    except Exception as e:
                        logger.debug(f"初步健康检查失败: {e}")
                        time.sleep(2)
                
                if not initial_check_passed:
                    msg = f"超时: 模型 '{primary_name}' 的 API 服务未在 {timeout_seconds} 秒内响应。"
                    logger.error(msg)
                    self.stop_model(alias)
                    return False, msg
                
                deep_check_success, deep_check_message = self._perform_deep_health_check(
                    alias, model_config, start_time, timeout_seconds
                )

                if deep_check_success:
                    logger.info(f"模型 '{primary_name}' 健康检查完全通过，服务已就绪！")
                    with state['lock']:
                        state.update({"status": "running", "last_access": time.time()})
                    return True, f"模型 {primary_name} 启动成功。"
                else:
                    logger.error(f"模型 '{primary_name}' 未能通过深度健康检查: {deep_check_message}")
                    self.stop_model(alias)
                    return False, f"启动模型 {primary_name} 失败: {deep_check_message}"

            except Exception as e:
                with state['lock']:
                    state['status'] = "stopped"
                logger.error(f"启动模型 {primary_name} 失败: {e}", exc_info=True)
                state['output_log'].append(f"[FATAL] 启动失败: {e}")
                if 'process' in locals() and process.poll() is None:
                    self.stop_model(alias)
                return False, f"启动模型 {primary_name} 失败: {e}"

    def stop_model(self, alias: str):
        primary_name = self._resolve_primary_name(alias)
        state = self.models_state[primary_name]
        with state['lock']:
            if state['status'] in ["stopped", "stopping"]:
                return True, f"模型 '{primary_name}' 已停止或正在停止中。"
            pid = state.get('pid')
            if pid:
                logger.info(f"正在停止模型 {primary_name} (PID: {pid})...")
                state['status'] = 'stopping'
                try:
                    subprocess.run(f"taskkill /F /T /PID {pid}", check=True, shell=True, capture_output=True, text=True, encoding='utf-8', errors='ignore')
                    logger.info(f"模型 {primary_name} (PID: {pid}) 已成功终止。")
                except subprocess.CalledProcessError as e:
                    logger.warning(f"终止模型 {primary_name} PID:{pid} 时出错: {e.stderr or e.stdout}")
            self.mark_model_as_stopped(alias, acquire_lock=False)
            return True, f"模型 {primary_name} 已停止。"

    def mark_model_as_stopped(self, alias: str, acquire_lock=True):
        primary_name = self._resolve_primary_name(alias)
        state = self.models_state[primary_name]
        def update():
            state.update({
                "process": None, "pid": None, "status": "stopped",
                "last_access": None, "pending_requests": state.get('pending_requests', 0), # 保留挂起的请求数
                "log_thread": None
            })
        if acquire_lock:
            with state['lock']: update()
        else:
            update()

    def unload_all_models(self):
        logger.info("正在卸载所有运行中的模型...")
        primary_names = list(self.models_state.keys())
        threads = [threading.Thread(target=self.stop_model, args=(name,)) for name in primary_names if self.models_state[name]['status'] != 'stopped']
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        logger.info("所有模型均已卸载。")

    def increment_pending_requests(self, alias: str):
        primary_name = self._resolve_primary_name(alias)
        state = self.models_state[primary_name]
        with state['lock']:
            state['pending_requests'] += 1
            logger.info(f"模型 {primary_name} 新请求进入，当前待处理: {state['pending_requests']}")

    def mark_request_completed(self, alias: str):
        primary_name = self._resolve_primary_name(alias)
        state = self.models_state[primary_name]
        with state['lock']:
            state['pending_requests'] = max(0, state['pending_requests'] - 1)
            state['last_access'] = time.time()
            logger.info(f"模型 {primary_name} 请求完成，剩余待处理: {state['pending_requests']}")

    def idle_check_loop(self):
        while self.is_running:
            time.sleep(30)
            if not self.is_running: break
            try:
                alive_time_min = self.config['program'].get('alive_time', 0)
                if alive_time_min <= 0: continue
                alive_time_sec = alive_time_min * 60
                now = time.time()
                for name in list(self.models_state.keys()):
                    state = self.models_state[name]
                    with state['lock']:
                        is_idle = (state['status'] == 'running' and
                                   state['last_access'] and
                                   state.get('pending_requests', 0) == 0)
                        if is_idle and (now - state['last_access']) > alive_time_sec:
                            logger.info(f"模型 {name} 空闲超过 {alive_time_min} 分钟，正在自动关闭...")
                            threading.Thread(target=self.stop_model, args=(name,)).start()
            except Exception as e:
                logger.error(f"空闲检查线程出错: {e}", exc_info=True)

    def get_all_models_status(self):
        status_copy = {}
        now = time.time()
        for primary_name, state in self.models_state.items():
            idle_seconds = (now - state['last_access']) if state.get('last_access') else -1
            config = self.get_adaptive_model_config(primary_name)
            status_copy[primary_name] = {
                "aliases": config.get("aliases", [primary_name]) if config else [primary_name],
                "status": state['status'],
                "pid": state['pid'],
                "idle_time_sec": f"{idle_seconds:.0f}" if idle_seconds != -1 else "N/A",
                "pending_requests": state.get('pending_requests', 0),
                "mode": config.get("mode", "Chat") if config else "Chat",
                "is_available": bool(config),  # 是否有可用配置
                "current_bat_path": config.get("bat_path", "") if config else "无可用配置",
                "config_source": config.get("config_source", "N/A") if config else "N/A"
            }
        return status_copy

    def get_model_log(self, primary_name: str):
        if primary_name in self.models_state:
            return self.models_state[primary_name].get('output_log', [])
        return ["错误：未找到指定的模型。"]

    def get_model_list(self):
        data = []
        for primary_name in self.models_state.keys():
            config = self.get_adaptive_model_config(primary_name)
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
        self.is_running = False
        self.unload_all_models()
        logger.info("ModelManager 已关闭。")

    def _check_and_free_vram(self, model_to_start_config):
        for attempt in range(2):
            available_mem = get_available_vram()
            required_mem = model_to_start_config.get("gpu_mem_mb", {})
            vram_deficit = {}
            for config_key, mem_req in required_mem.items():
                if mem_req <= 0: continue
                gpu = self.gpu_map.get(config_key.lower().strip())
                if not gpu:
                    logger.warning(f"配置中的GPU '{config_key}' 未在系统中找到，跳过。")
                    continue
                if available_mem.get(gpu.id, 0) < mem_req:
                    deficit = mem_req - available_mem.get(gpu.id, 0)
                    vram_deficit[gpu.id] = max(vram_deficit.get(gpu.id, 0), deficit)
            
            if not vram_deficit:
                logger.info("显存充足。")
                return True
            
            logger.warning(f"显存不足，需要释放: {vram_deficit}")
            if attempt == 0:
                logger.info("尝试停止空闲模型以释放显存...")
                self._stop_idle_models_for_vram(vram_deficit)
                time.sleep(2) 
            else:
                logger.error(f"释放显存后，仍无法为模型 {model_to_start_config['aliases'][0]} 提供足够空间。")
                return False
        return False

    def _stop_idle_models_for_vram(self, vram_deficit):
        idle_candidates = [
            name for name, state in self.models_state.items()
            if state['status'] == 'running' and state.get('pending_requests', 0) == 0
        ]
        sorted_idle_models = sorted(idle_candidates, key=lambda m: self.models_state[m]['last_access'] or 0)
        
        if not sorted_idle_models:
            logger.warning("没有可供停止的空闲模型。")
            return

        for model_name in sorted_idle_models:
            model_config_to_stop = self.get_model_config(model_name)
            if not model_config_to_stop: continue
            
            logger.info(f"为释放显存，正在停止空闲模型: {model_name}")
            self.stop_model(model_name)
            
            freed_mem = model_config_to_stop.get("gpu_mem_mb", {})
            for config_key, mem_val in freed_mem.items():
                gpu = self.gpu_map.get(config_key.lower().strip())
                if gpu and gpu.id in vram_deficit:
                    vram_deficit[gpu.id] -= mem_val
            
            if all(deficit <= 0 for deficit in vram_deficit.values()):
                logger.info("已成功释放足够的显存。")
                return