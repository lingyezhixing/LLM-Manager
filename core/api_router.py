from fastapi import Request, HTTPException, Response
from fastapi.responses import StreamingResponse
from typing import Dict, Set
import json
import time
import asyncio
import re
from utils.logger import get_logger
from core.model_controller import ModelController
from core.config_manager import ConfigManager
from core.data_manager import Monitor

logger = get_logger(__name__)

class TokenTracker:
    """Token消耗跟踪器 - 负责Token的解析提取与异步记录 (优先 timings，降级 usage)"""

    def __init__(self, monitor: Monitor, config_manager: ConfigManager):
        self.monitor = monitor
        self.config_manager = config_manager
        logger.info(f"[TokenTracker] 初始化完成, 当前追踪模式: {self.config_manager.get_token_tracker_modes()}")

    def _extract_tokens(self, data: dict) -> tuple[int, int, int, int]:
        """从字典中提取Token信息"""
        # 1. 优先尝试从 timings 获取
        if "timings" in data:
            timings = data["timings"]
            cache_n = timings.get("cache_n", 0)
            prompt_n = timings.get("prompt_n", 0)
            predicted_n = timings.get("predicted_n", 0)

            input_tokens = cache_n + prompt_n
            output_tokens = predicted_n

            if any([input_tokens, output_tokens, cache_n, prompt_n]):
                logger.debug(f"[TokenTracker] 解析到 timings: input={input_tokens}, output={output_tokens}, cache_n={cache_n}, prompt_n={prompt_n}")
                return input_tokens, output_tokens, cache_n, prompt_n

        # 2. 降级尝试从 usage 获取
        if "usage" in data:
            usage = data["usage"]
            input_tokens = usage.get("prompt_tokens", 0)
            output_tokens = usage.get("completion_tokens", 0)

            if any([input_tokens, output_tokens]):
                logger.debug(f"[TokenTracker] 解析到 usage: input={input_tokens}, output={output_tokens}")
                return input_tokens, output_tokens, 0, 0
                
        return 0, 0, 0, 0

    def extract_tokens_from_response(self, response_content: bytes) -> tuple[int, int, int, int]:
        """从响应中提取Token (包含针对SSE/JSON的倒序解析与Debug打印)"""
        try:
            content_str = response_content.decode('utf-8')
            logger.debug(f"[TokenTracker] 开始解析响应, 大小: {len(response_content)} bytes")

            def get_reversed_blocks():
                """生成器：倒序获取响应中的有效数据块"""
                if "data: " in content_str:
                    for line in reversed(content_str.splitlines()):
                        if line.startswith('data: '):
                            data_str = line[6:].strip()
                            if data_str and data_str != "[DONE]":
                                yield data_str
                else:
                    try:
                        json.loads(content_str)
                        yield content_str
                    except json.JSONDecodeError:
                        json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
                        for match in reversed(re.findall(json_pattern, content_str)):
                            yield match

            block_generator = get_reversed_blocks()
            
            # 预拉取末尾最多10个块用于Debug
            first_10_blocks = []
            for _ in range(10):
                try:
                    first_10_blocks.append(next(block_generator))
                except StopIteration:
                    break

            if not first_10_blocks:
                logger.debug("[TokenTracker] 未匹配到任何有效数据块")
                return 0, 0, 0, 0

            # Debug日志：倒序打印末尾数据块，优化多层级格式提升可读性
            debug_logs = ["[TokenTracker] 末尾数据块 (倒序输出, 最多10个):"]
            for i, block_str in enumerate(first_10_blocks):
                try:
                    parsed_debug = json.loads(block_str)
                    pretty_json = json.dumps(parsed_debug, indent=2, ensure_ascii=False)
                    debug_logs.append(f"--- 倒数第 {i+1} 块 ---\n{pretty_json}")
                except json.JSONDecodeError:
                    debug_logs.append(f"--- 倒数第 {i+1} 块 (非合法JSON) ---\n{block_str}")
            logger.debug("\n".join(debug_logs))

            def _parse_blocks(blocks) -> tuple[int, int, int, int] | None:
                """内部辅助：遍历指定区块尝试提取Token"""
                for block in blocks:
                    try:
                        result = self._extract_tokens(json.loads(block))
                        if any(result):
                            return result
                    except json.JSONDecodeError:
                        continue
                return None

            # 1. 优先在预拉取的末尾10个块中寻找
            result = _parse_blocks(first_10_blocks)
            if result:
                return result

            # 2. 如果未找到，继续遍历剩余迭代器
            result = _parse_blocks(block_generator)
            if result:
                return result

            logger.debug("[TokenTracker] 未找到有效的 Token 统计信息")
            return 0, 0, 0, 0

        except Exception as e:
            logger.error(f"[TokenTracker] Token提取异常: {e}")
            return 0, 0, 0, 0

    async def record_request_tokens(self, model_name: str, input_tokens: int, output_tokens: int, cache_n: int = 0, prompt_n: int = 0, start_time: float = 0.0, end_time: float = 0.0):
        """异步记录请求Token到数据库"""
        try:
            final_end_time = end_time if end_time > 0 else time.time()
            final_start_time = start_time if start_time > 0 else final_end_time

            model_mode = self.config_manager.get_model_mode(model_name)
            if not self.config_manager.should_track_tokens_for_mode(model_mode):
                logger.debug(f"[TokenTracker] 忽略记录: 模型 {model_name} (模式 {model_mode}) 不在追踪列表中")
                return

            if not any([input_tokens, output_tokens, cache_n, prompt_n]):
                logger.debug("[TokenTracker] 忽略记录: 提取的Token数均为0")
                return

            await asyncio.to_thread(
                self.monitor.add_model_request,
                model_name,
                final_start_time,
                final_end_time,
                input_tokens,
                output_tokens,
                cache_n,
                prompt_n
            )

            logger.debug(f"[TokenTracker] 记录成功: 模型 {model_name} (模式 {model_mode}), 总Tokens {input_tokens + output_tokens}, cache_n: {cache_n}, prompt_n: {prompt_n}")
        except Exception as e:
            logger.error(f"[TokenTracker] 记录失败: 模型 {model_name}, 错误: {e}")

    async def create_stream_with_token_logging(self, model_name: str, response: any, request_start_time: float):
        """流式响应包装器：转发流数据并在结束后提取、记录Token"""
        content_chunks = []
        try:
            logger.debug(f"[TokenTracker] 开始处理流式响应: 模型 {model_name}")
            async for chunk in response.aiter_bytes():
                content_chunks.append(chunk)
                yield chunk
        finally:
            request_end_time = time.time()
            await response.aclose()
            logger.debug(f"[TokenTracker] 流式响应结束: 模型 {model_name}, 共收到 {len(content_chunks)} 个数据块")

            try:
                full_content = b''.join(content_chunks)
                input_tokens, output_tokens, cache_n, prompt_n = self.extract_tokens_from_response(full_content)

                if any([input_tokens, output_tokens, cache_n, prompt_n]):
                    await self.record_request_tokens(model_name, input_tokens, output_tokens, cache_n, prompt_n, request_start_time, request_end_time)
                else:
                    logger.debug(f"[TokenTracker] 跳过流响应Token记录: 模型 {model_name}, Token提取结果全为0")

            except Exception as e:
                logger.error(f"[TokenTracker] 流式响应提取与记录失败: 模型 {model_name}, 错误: {e}")

class APIRouter:
    """API路由器 - 负责请求路由和转发"""

    def __init__(self, config_manager: ConfigManager, model_controller: ModelController):
        self.config_manager = config_manager
        self.model_controller = model_controller
        self.async_clients: Dict[int, any] = {}
        self.pending_requests: Dict[str, int] = {}
        
        # 【新增】本地启动任务标记，防止高并发请求耗尽线程池
        self.starting_models: Set[str] = set()
        
        logger.info("API路由器初始化完成")

    async def get_async_client(self, port: int):
        """获取异步HTTP客户端"""
        if port not in self.async_clients:
            import httpx
            # 增加连接超时时间，适应模型启动等待时间
            timeouts = httpx.Timeout(30.0, read=600.0, connect=30.0, write=30.0)
            self.async_clients[port] = httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                timeout=timeouts
            )
        return self.async_clients[port]

    # 【新增辅助方法】用于安全更新模型最后活动时间
    def _touch_model_activity(self, model_name: str):
        """更新模型的最后活动时间戳"""
        if model_name in self.model_controller.models_state:
            state = self.model_controller.models_state[model_name]
            # 获取锁更新时间戳，确保线程安全
            with state['lock']:
                state['last_access'] = time.time()
                # logger.debug(f"模型 {model_name} 活动时间已刷新")

    def increment_pending_requests(self, model_name: str):
        """增加待处理请求计数"""
        if model_name not in self.pending_requests:
            self.pending_requests[model_name] = 0
        self.pending_requests[model_name] += 1
        
        # 【修改点 1：请求到达时更新时间戳】
        self._touch_model_activity(model_name)
        
        logger.info(f"模型 {model_name} 新请求进入，当前待处理: {self.pending_requests[model_name]}")

    def mark_request_completed(self, model_name: str):
        """标记请求完成"""
        if model_name in self.pending_requests:
            self.pending_requests[model_name] = max(0, self.pending_requests[model_name] - 1)
            
            # 【修改点 2：请求结束时更新时间戳】
            # 这样倒计时会从任务完成的那一刻开始计算，而不是任务开始时
            self._touch_model_activity(model_name)
            
            logger.info(f"模型 {model_name} 请求完成，剩余待处理: {self.pending_requests[model_name]}")

    async def route_request(self, request: Request, path: str, token_tracker: TokenTracker) -> Response:
        """路由请求到目标模型，并记录精确的起止时间"""
        if request.method == "OPTIONS":
            return Response(status_code=204, headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "*",
                "Access-Control-Allow-Headers": "*"
            })

        body, model_alias, request_data = {}, None, b''
        try:
            if "application/json" in request.headers.get("content-type", ""):
                body = await request.json()
                model_alias = body.get("model")
                request_data = json.dumps(body).encode('utf-8')
            else:
                request_data = await request.body()
        except Exception:
            request_data = await request.body()

        if not model_alias:
            raise HTTPException(status_code=400, detail="请求体(JSON)中缺少 'model' 字段")

        try:
            model_name = self.config_manager.resolve_primary_name(model_alias)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"模型别名 '{model_alias}' 未在配置中找到")

        model_config = self.config_manager.get_model_config(model_name)
        if not model_config:
            raise HTTPException(status_code=404, detail=f"模型 '{model_name}' 配置未找到")

        model_mode = model_config.get("mode", "Chat")
        interface_plugin = self.model_controller.plugin_manager.get_interface_plugin(model_mode)
        if not interface_plugin:
            raise HTTPException(status_code=400, detail=f"不支持的模型模式: {model_mode}")

        is_valid, error_message = interface_plugin.validate_request(path, model_name)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_message)

        self.increment_pending_requests(model_name)
        request_start_time = time.time()  # 在请求转发前记录开始时间

        try:
            # ==================== 智能启动控制逻辑 ====================
            # 解决高并发导致线程池耗尽的问题：
            # 1. 检查模型状态，如果正在启动（全局状态）或 本路由正在处理启动（本地状态），则异步等待。
            # 2. 只有当状态为停止且本地没有启动任务时，才分配一个线程去执行启动。
            
            while True:
                # 1. 获取当前模型状态
                model_state = self.model_controller.models_state.get(model_name, {})
                current_status = model_state.get('status', 'stopped')
                
                # 2. 如果模型已就绪，直接跳出循环处理请求
                if current_status == 'routing':
                    break
                
                # 3. 检查是否有启动任务正在进行
                # A: 控制器已经标记为启动中 (STARTING, INIT_SCRIPT, HEALTH_CHECK)
                is_starting_global = current_status in ['starting', 'init_script', 'health_check']
                # B: 路由器刚才已经派发了一个启动线程（闭合时间窗口的关键！）
                is_starting_local = model_name in self.starting_models
                
                if is_starting_global or is_starting_local:
                    # 发现正在启动，异步等待，绝对不占用线程池资源
                    # 使用 asyncio.sleep 让出控制权
                    logger.debug(f"[API_ROUTER] 模型 {model_name} 正在启动中 (Status: {current_status}), 异步等待...")
                    await asyncio.sleep(0.5)
                    continue
                
                # 4. 只有状态为停止/失败，且本地没有正在进行的启动任务时，才发起启动
                if current_status in ['stopped', 'failed']:
                    # 【关键】先在本地打标记，瞬间锁住后续并发请求进入此分支
                    self.starting_models.add(model_name)
                    try:
                        logger.info(f"[API_ROUTER] 模型 {model_name} 需要启动，分配唯一启动线程...")
                        # 这是一个耗时操作，占用 1 个线程
                        success, message = await asyncio.to_thread(
                            self.model_controller.start_model, model_name
                        )
                        if not success:
                            raise HTTPException(status_code=503, detail=message)
                        # 启动成功后，下一次循环会检测到 routing 状态并 break
                    except Exception as e:
                        logger.error(f"[API_ROUTER] 启动模型异常: {e}")
                        # 如果是我们自己抛出的 HTTPException，直接重抛
                        if isinstance(e, HTTPException):
                            raise e
                        raise HTTPException(status_code=503, detail=f"启动异常: {str(e)}")
                    finally:
                        # 无论成功失败，移除本地标记，允许后续逻辑处理或重试
                        if model_name in self.starting_models:
                            self.starting_models.remove(model_name)
                    continue
                
                # 防止死循环的兜底等待
                await asyncio.sleep(0.5)
            # ==================== 启动控制结束 ====================

            target_port = model_config['port']
            client = await self.get_async_client(target_port)

            target_url = client.base_url.join(path)
            headers = dict(request.headers)
            headers.pop("host", None)
            headers.pop("content-length", None)
            headers.pop("transfer-encoding", None)

            req = client.build_request(
                request.method,
                target_url,
                headers=headers,
                content=request_data,
                params=request.query_params
            )

            response = await client.send(req, stream=True)
            is_streaming = "text/event-stream" in response.headers.get("content-type", "")

            if is_streaming:
                # 为流式响应创建一个包装生成器，以确保在流结束后减少请求计数
                async def stream_wrapper():
                    # TokenTracker的生成器负责Token记录
                    token_logging_stream = token_tracker.create_stream_with_token_logging(
                        model_name, response, request_start_time
                    )
                    try:
                        async for chunk in token_logging_stream:
                            yield chunk
                    finally:
                        # 此代码块在流完全消耗或关闭后执行
                        self.mark_request_completed(model_name)
                        logger.debug(f"模型 '{model_name}' 的流式响应已完成，请求计数已递减。")

                return StreamingResponse(
                    stream_wrapper(),
                    status_code=response.status_code,
                    headers=dict(response.headers)
                )
            else:
                logger.debug(f"[API_ROUTER] 开始处理非流式响应 - 模型: {model_name}")
                content = await response.aread()
                request_end_time = time.time()  # 在读取完响应后记录结束时间
                await response.aclose()
                self.mark_request_completed(model_name)
                logger.debug(f"[API_ROUTER] 非流式响应读取完成 - 模型: {model_name}, 响应大小: {len(content)} bytes")

                try:
                    input_tokens, output_tokens, cache_n, prompt_n = token_tracker.extract_tokens_from_response(content)
                    if input_tokens > 0 or output_tokens > 0 or cache_n > 0 or prompt_n > 0:
                        logger.debug(f"[API_ROUTER] 准备异步记录非流式响应token - 模型: {model_name}, 总token数: {input_tokens + output_tokens}, cache_n: {cache_n}, prompt_n: {prompt_n}")
                        # 传递开始和结束时间
                        await token_tracker.record_request_tokens(model_name, input_tokens, output_tokens, cache_n, prompt_n, request_start_time, request_end_time)
                    else:
                        logger.debug(f"[API_ROUTER] 跳过非流式响应token记录 - 模型: {model_name}, 所有token数为0")
                except Exception as e:
                    logger.error(f"[API_ROUTER] 非流式响应token记录失败 - 模型: {model_name}, 错误: {e}")

                return Response(
                    content=content,
                    status_code=response.status_code,
                    headers=dict(response.headers)
                )

        except Exception as e:
            logger.error(f"处理对 '{model_name}' 的请求时出错: {e}", exc_info=True)
            self.mark_request_completed(model_name)
            if isinstance(e, HTTPException):
                raise e
            raise HTTPException(status_code=500, detail=f"内部服务器错误: {str(e)}")