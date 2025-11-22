from fastapi import Request, HTTPException, Response
from fastapi.responses import StreamingResponse
from typing import Dict, Any
import json
import time
import asyncio
from utils.logger import get_logger
from core.model_controller import ModelController
from core.config_manager import ConfigManager
from core.data_manager import Monitor

logger = get_logger(__name__)


class TokenTracker:
    """Token消耗跟踪器 - 负责token提取和记录"""

    def __init__(self, monitor: Monitor, config_manager: ConfigManager):
        self.monitor = monitor
        self.config_manager = config_manager
        logger.info("Token跟踪器初始化完成")
        logger.info(f"Token追踪模式: {self.config_manager.get_token_tracker_modes()}")

    def extract_tokens_from_usage(self, usage_data: Dict[str, Any]) -> tuple[int, int, int, int]:
        """从usage数据中提取token数量"""
        prompt_tokens = usage_data.get("prompt_tokens", 0)
        completion_tokens = usage_data.get("completion_tokens", 0)
        cache_n = usage_data.get("cache_n", 0)
        prompt_n = usage_data.get("prompt_n", 0)
        return prompt_tokens, completion_tokens, cache_n, prompt_n

    def extract_tokens_from_response(self, response_content: bytes) -> tuple[int, int, int, int]:
        """从响应内容中提取token信息"""
        try:
            content_str = response_content.decode('utf-8')
            logger.debug(f"[TOKEN_TRACKER] 开始提取token信息，响应大小: {len(response_content)} bytes")

            # 处理SSE格式响应
            if "data: " in content_str:
                logger.debug(f"[TOKEN_TRACKER] 检测到SSE格式响应")

                # 从后向前查找包含usage/timings的JSON数据，跳过[DONE]标记
                lines = content_str.split('\n')
                for line in reversed(lines):
                    if not line.startswith('data: '):
                        continue

                    data_str = line[6:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue

                    try:
                        data = json.loads(data_str)
                        # 检查是否包含token相关信息
                        if "usage" in data or "timings" in data:
                            input_tokens = output_tokens = cache_n = prompt_n = 0

                            # 从usage字段提取token数量
                            if "usage" in data:
                                usage = data["usage"]
                                input_tokens = usage.get("prompt_tokens", 0)
                                output_tokens = usage.get("completion_tokens", 0)
                                logger.debug(f"[TOKEN_TRACKER] 从usage提取到: input={input_tokens}, output={output_tokens}")

                            # 从timings字段提取cache_n和prompt_n
                            if "timings" in data:
                                timings = data["timings"]
                                cache_n = timings.get("cache_n", 0)
                                prompt_n = timings.get("prompt_n", 0)
                                logger.debug(f"[TOKEN_TRACKER] 从timings提取到: cache_n={cache_n}, prompt_n={prompt_n}")

                            if input_tokens > 0 or output_tokens > 0 or cache_n > 0 or prompt_n > 0:
                                logger.debug(f"[TOKEN_TRACKER] SSE解析成功: {input_tokens}, {output_tokens}, {cache_n}, {prompt_n}")
                                return input_tokens, output_tokens, cache_n, prompt_n
                    except json.JSONDecodeError:
                        continue

            # 处理普通JSON响应
            try:
                data = json.loads(content_str)
                input_tokens = output_tokens = cache_n = prompt_n = 0

                # 从usage字段提取token数量
                if "usage" in data:
                    usage = data["usage"]
                    input_tokens = usage.get("prompt_tokens", 0)
                    output_tokens = usage.get("completion_tokens", 0)
                    logger.debug(f"[TOKEN_TRACKER] 从usage提取到: input={input_tokens}, output={output_tokens}")

                # 从timings字段提取cache_n和prompt_n
                if "timings" in data:
                    timings = data["timings"]
                    cache_n = timings.get("cache_n", 0)
                    prompt_n = timings.get("prompt_n", 0)
                    logger.debug(f"[TOKEN_TRACKER] 从timings提取到: cache_n={cache_n}, prompt_n={prompt_n}")

                if input_tokens > 0 or output_tokens > 0 or cache_n > 0 or prompt_n > 0:
                    logger.debug(f"[TOKEN_TRACKER] JSON解析成功: {input_tokens}, {output_tokens}, {cache_n}, {prompt_n}")
                    return input_tokens, output_tokens, cache_n, prompt_n
            except json.JSONDecodeError:
                logger.debug(f"[TOKEN_TRACKER] 普通JSON解析失败，尝试正则提取")

            # 使用正则表达式提取JSON对象
            import re
            json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
            json_matches = re.findall(json_pattern, content_str)

            for match in json_matches:
                try:
                    data = json.loads(match)
                    input_tokens = output_tokens = cache_n = prompt_n = 0

                    # 从usage字段提取token数量
                    if "usage" in data:
                        usage = data["usage"]
                        input_tokens = usage.get("prompt_tokens", 0)
                        output_tokens = usage.get("completion_tokens", 0)
                        logger.debug(f"[TOKEN_TRACKER] 正则提取到usage: input={input_tokens}, output={output_tokens}")

                    # 从timings字段提取cache_n和prompt_n
                    if "timings" in data:
                        timings = data["timings"]
                        cache_n = timings.get("cache_n", 0)
                        prompt_n = timings.get("prompt_n", 0)
                        logger.debug(f"[TOKEN_TRACKER] 正则提取到timings: cache_n={cache_n}, prompt_n={prompt_n}")

                    if input_tokens > 0 or output_tokens > 0 or cache_n > 0 or prompt_n > 0:
                        logger.debug(f"[TOKEN_TRACKER] 正则提取成功: {input_tokens}, {output_tokens}, {cache_n}, {prompt_n}")
                        return input_tokens, output_tokens, cache_n, prompt_n
                except json.JSONDecodeError:
                    continue

            logger.debug(f"[TOKEN_TRACKER] 未找到有效的token信息")
            return 0, 0, 0, 0

        except Exception as e:
            logger.debug(f"[TOKEN_TRACKER] Token提取失败: {e}")
            return 0, 0, 0, 0

    async def record_request_tokens(self, model_name: str, input_tokens: int, output_tokens: int, cache_n: int = 0, prompt_n: int = 0, start_time: float = 0.0, end_time: float = 0.0):
        """【已修改】异步记录请求token到数据库，包含起止时间"""
        try:
            # 使用传入的结束时间，如果未提供则使用当前时间
            final_end_time = end_time if end_time > 0 else time.time()
            # 使用传入的开始时间，如果未提供则用结束时间作为回退
            final_start_time = start_time if start_time > 0 else final_end_time

            # 检查模型模式是否需要追踪token
            model_mode = self.config_manager.get_model_mode(model_name)
            if not self.config_manager.should_track_tokens_for_mode(model_mode):
                logger.debug(f"[TOKEN_TRACKER] 跳过记录 - 模型 {model_name} 的模式 {model_mode} 不在追踪列表中")
                return

            logger.debug(f"[TOKEN_TRACKER] 开始异步记录token - 模型: {model_name}, 模式: {model_mode}")
            logger.debug(f"[TOKEN_TRACKER] Token信息 - 输入: {input_tokens}, 输出: {output_tokens}, cache_n: {cache_n}, prompt_n: {prompt_n}, start: {final_start_time}, end: {final_end_time}")

            # 检查是否有token需要记录
            if input_tokens == 0 and output_tokens == 0 and cache_n == 0 and prompt_n == 0:
                logger.debug(f"[TOKEN_TRACKER] 跳过记录 - 所有token数为0")
                return

            # 使用异步方式写入数据库
            logger.debug(f"[TOKEN_TRACKER] 调用monitor.add_model_request方法")
            await asyncio.to_thread(
                self.monitor.add_model_request,
                model_name,
                [final_start_time, final_end_time, input_tokens, output_tokens, cache_n, prompt_n]
            )

            logger.debug(f"[TOKEN_TRACKER] 异步记录token成功 - 模型: {model_name}, 模式: {model_mode}, 总token数: {input_tokens + output_tokens}, cache_n: {cache_n}, prompt_n: {prompt_n}")
        except Exception as e:
            logger.error(f"[TOKEN_TRACKER] 异步记录token失败 - 模型: {model_name}, 错误: {e}")

    async def create_stream_with_token_logging(self, model_name: str, response: any, request_start_time: float):
        """【已修改】流式响应包装器，在结束后记录token使用，并传递开始时间"""
        content_chunks = []
        try:
            logger.debug(f"[TOKEN_TRACKER] 开始流式响应处理 - 模型: {model_name}")
            async for chunk in response.aiter_bytes():
                content_chunks.append(chunk)
                yield chunk
        finally:
            request_end_time = time.time() # 记录流式响应结束时间
            await response.aclose()
            logger.debug(f"[TOKEN_TRACKER] 流式响应结束 - 模型: {model_name}, 收到 {len(content_chunks)} 个数据块")

            # 处理token记录
            try:
                full_content = b''.join(content_chunks)
                logger.debug(f"[TOKEN_TRACKER] 合并流式响应内容，总大小: {len(full_content)} bytes")

                input_tokens, output_tokens, cache_n, prompt_n = self.extract_tokens_from_response(full_content)

                if input_tokens > 0 or output_tokens > 0 or cache_n > 0 or prompt_n > 0:
                    logger.debug(f"[TOKEN_TRACKER] 准备异步记录token - 模型: {model_name}, 总token数: {input_tokens + output_tokens}, cache_n: {cache_n}, prompt_n: {prompt_n}")
                    # 传递开始和结束时间
                    await self.record_request_tokens(model_name, input_tokens, output_tokens, cache_n, prompt_n, request_start_time, request_end_time)
                else:
                    logger.debug(f"[TOKEN_TRACKER] 跳过token记录 - 模型: {model_name}, 所有token数为0")

            except Exception as e:
                logger.error(f"[TOKEN_TRACKER] 流式响应token记录失败 - 模型: {model_name}, 错误: {e}")


class APIRouter:
    """API路由器 - 负责请求路由和转发"""

    def __init__(self, config_manager: ConfigManager, model_controller: ModelController):
        self.config_manager = config_manager
        self.model_controller = model_controller
        self.async_clients: Dict[int, any] = {}
        self.pending_requests: Dict[str, int] = {}
        logger.info("API路由器初始化完成")

    async def get_async_client(self, port: int):
        """获取异步HTTP客户端"""
        if port not in self.async_clients:
            import httpx
            # 【调整】增加连接超时时间，适应模型启动等待时间
            timeouts = httpx.Timeout(30.0, read=600.0, connect=30.0, write=30.0)
            self.async_clients[port] = httpx.AsyncClient(
                base_url=f"http://127.0.0.1:{port}",
                timeout=timeouts
            )
        return self.async_clients[port]

    def increment_pending_requests(self, model_name: str):
        """增加待处理请求计数"""
        if model_name not in self.pending_requests:
            self.pending_requests[model_name] = 0
        self.pending_requests[model_name] += 1
        logger.info(f"模型 {model_name} 新请求进入，当前待处理: {self.pending_requests[model_name]}")

    def mark_request_completed(self, model_name: str):
        """标记请求完成"""
        if model_name in self.pending_requests:
            self.pending_requests[model_name] = max(0, self.pending_requests[model_name] - 1)
            logger.info(f"模型 {model_name} 请求完成，剩余待处理: {self.pending_requests[model_name]}")

    async def route_request(self, request: Request, path: str, token_tracker: TokenTracker) -> Response:
        """【已修改】路由请求到目标模型，并记录精确的起止时间"""
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
        request_start_time = time.time()  # 【新增】在请求转发前记录开始时间

        try:
            success, message = await asyncio.to_thread(
                self.model_controller.start_model, model_name
            )
            if not success:
                raise HTTPException(status_code=503, detail=message)

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
                # 【修复】为流式响应创建一个包装生成器，以确保在流结束后减少请求计数
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
                request_end_time = time.time()  # 【新增】在读取完响应后记录结束时间
                await response.aclose()
                self.mark_request_completed(model_name)
                logger.debug(f"[API_ROUTER] 非流式响应读取完成 - 模型: {model_name}, 响应大小: {len(content)} bytes")

                try:
                    input_tokens, output_tokens, cache_n, prompt_n = token_tracker.extract_tokens_from_response(content)
                    if input_tokens > 0 or output_tokens > 0 or cache_n > 0 or prompt_n > 0:
                        logger.debug(f"[API_ROUTER] 准备异步记录非流式响应token - 模型: {model_name}, 总token数: {input_tokens + output_tokens}, cache_n: {cache_n}, prompt_n: {prompt_n}")
                        # 【修改】传递开始和结束时间
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