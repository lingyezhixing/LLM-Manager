from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import StreamingResponse, JSONResponse
from typing import Dict, List, Optional, Any
import json
import time
import asyncio
from utils.logger import get_logger
from core.model_controller import ModelController
from core.config_manager import ConfigManager
from core.monitor import Monitor

logger = get_logger(__name__)


class TokenTracker:
    """Token消耗跟踪器 - 负责token提取和记录"""

    def __init__(self, monitor: Monitor):
        self.monitor = monitor
        logger.info("Token跟踪器初始化完成")

    def extract_tokens_from_usage(self, usage_data: Dict[str, Any]) -> tuple[int, int]:
        """从usage数据中提取输入和输出token数量"""
        prompt_tokens = usage_data.get("prompt_tokens", 0)
        completion_tokens = usage_data.get("completion_tokens", 0)
        return prompt_tokens, completion_tokens

    def extract_tokens_from_response(self, response_content: bytes) -> tuple[int, int]:
        """从响应内容中提取token信息"""
        try:
            logger.debug(f"[TOKEN_TRACKER] 开始提取token信息，响应大小: {len(response_content)} bytes")

            # 处理流式响应（SSE格式）
            content_str = response_content.decode('utf-8')

            # 如果是SSE格式，需要提取最后的JSON数据
            if "data: " in content_str:
                logger.debug(f"[TOKEN_TRACKER] 检测到SSE格式响应，尝试提取JSON数据")

                # 找到最后一个data: 行，通常包含完整的usage信息
                lines = content_str.split('\n')
                last_json_line = None

                for line in reversed(lines):
                    if line.startswith('data: '):
                        data_str = line[6:]  # 去掉 "data: " 前缀
                        if data_str.strip():  # 确保不是空行
                            last_json_line = data_str
                            break

                if last_json_line:
                    logger.debug(f"[TOKEN_TRACKER] 找到最后JSON数据: {last_json_line[:100]}...")
                    data = json.loads(last_json_line)
                else:
                    logger.debug(f"[TOKEN_TRACKER] 未找到有效的JSON数据行")
                    return 0, 0
            else:
                # 普通JSON响应
                logger.debug(f"[TOKEN_TRACKER] 检测到普通JSON响应")
                data = json.loads(content_str)

            logger.debug(f"[TOKEN_TRACKER] JSON解析成功，响应字段: {list(data.keys())}")

            # 检查是否有usage字段
            if "usage" in data:
                usage = data["usage"]
                logger.debug(f"[TOKEN_TRACKER] 找到usage字段: {usage}")
                input_tokens, output_tokens = self.extract_tokens_from_usage(usage)
                logger.debug(f"[TOKEN_TRACKER] Token提取成功 - 输入: {input_tokens}, 输出: {output_tokens}")
                return input_tokens, output_tokens

            # 如果没有usage字段，返回0
            logger.debug(f"[TOKEN_TRACKER] 响应中未找到usage字段")
            return 0, 0
        except json.JSONDecodeError as e:
            logger.debug(f"[TOKEN_TRACKER] JSON解析失败: {e}")
            # 尝试更宽松的解析方式
            try:
                content_str = response_content.decode('utf-8')
                # 尝试找到任何看起来像JSON的部分
                import re
                json_pattern = r'\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}'
                json_matches = re.findall(json_pattern, content_str)

                for match in json_matches:
                    try:
                        data = json.loads(match)
                        if "usage" in data:
                            usage = data["usage"]
                            logger.debug(f"[TOKEN_TRACKER] 通过正则提取找到usage字段: {usage}")
                            input_tokens, output_tokens = self.extract_tokens_from_usage(usage)
                            logger.debug(f"[TOKEN_TRACKER] Token提取成功 - 输入: {input_tokens}, 输出: {output_tokens}")
                            return input_tokens, output_tokens
                    except:
                        continue

                logger.debug(f"[TOKEN_TRACKER] 正则表达式也未找到有效的usage数据")
                return 0, 0
            except Exception as e2:
                logger.debug(f"[TOKEN_TRACKER] 正则表达式提取也失败: {e2}")
                return 0, 0
        except Exception as e:
            logger.debug(f"[TOKEN_TRACKER] Token提取失败: {e}")
            return 0, 0

    async def record_request_tokens(self, model_name: str, input_tokens: int, output_tokens: int):
        """异步记录请求token到数据库"""
        try:
            timestamp = time.time()

            logger.debug(f"[TOKEN_TRACKER] 开始异步记录token - 模型: {model_name}")
            logger.debug(f"[TOKEN_TRACKER] Token信息 - 输入: {input_tokens}, 输出: {output_tokens}, 时间戳: {timestamp}")

            # 检查是否有token需要记录
            if input_tokens == 0 and output_tokens == 0:
                logger.debug(f"[TOKEN_TRACKER] 跳过记录 - token数为0")
                return

            # 使用异步方式写入数据库
            logger.debug(f"[TOKEN_TRACKER] 调用monitor.add_model_request方法")
            await asyncio.to_thread(
                self.monitor.add_model_request,
                model_name,
                [timestamp, input_tokens, output_tokens]
            )

            logger.debug(f"[TOKEN_TRACKER] 异步记录token成功 - 模型: {model_name}, 总token数: {input_tokens + output_tokens}")
        except Exception as e:
            logger.error(f"[TOKEN_TRACKER] 异步记录token失败 - 模型: {model_name}, 错误: {e}")
            # 不抛出异常，避免影响主要请求流程

    async def create_stream_with_token_logging(self, model_name: str, response: any):
        """流式响应包装器，在结束后记录token使用"""
        content_chunks = []
        try:
            logger.debug(f"[TOKEN_TRACKER] 开始流式响应处理 - 模型: {model_name}")
            async for chunk in response.aiter_bytes():
                content_chunks.append(chunk)
                yield chunk
        finally:
            await response.aclose()
            logger.debug(f"[TOKEN_TRACKER] 流式响应结束 - 模型: {model_name}, 收到 {len(content_chunks)} 个数据块")

            # 处理token记录
            try:
                # 合并所有内容块
                full_content = b''.join(content_chunks)
                logger.debug(f"[TOKEN_TRACKER] 合并流式响应内容，总大小: {len(full_content)} bytes")

                # 提取token信息
                input_tokens, output_tokens = self.extract_tokens_from_response(full_content)

                # 异步记录到数据库
                if input_tokens > 0 or output_tokens > 0:
                    logger.debug(f"[TOKEN_TRACKER] 准备异步记录token - 模型: {model_name}, 总token数: {input_tokens + output_tokens}")
                    await self.record_request_tokens(model_name, input_tokens, output_tokens)
                else:
                    logger.debug(f"[TOKEN_TRACKER] 跳过token记录 - 模型: {model_name}, token数为0")

            except Exception as e:
                logger.error(f"[TOKEN_TRACKER] 流式响应token记录失败 - 模型: {model_name}, 错误: {e}")
                # 不抛出异常，避免影响主要请求流程


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
            timeouts = httpx.Timeout(10.0, read=600.0)
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
        """路由请求到目标模型"""
        # 处理OPTIONS请求
        if request.method == "OPTIONS":
            return Response(status_code=204, headers={
                "Access-Control-Allow-Origin": "*",
                "Access-Control-Allow-Methods": "*",
                "Access-Control-Allow-Headers": "*"
            })

        # 解析请求
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

        # 在API入口处解析别名为主名称
        try:
            model_name = self.config_manager.resolve_primary_name(model_alias)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"模型别名 '{model_alias}' 未在配置中找到")

        # 获取模型配置并验证模式
        model_config = self.config_manager.get_model_config(model_name)
        if not model_config:
            raise HTTPException(status_code=404, detail=f"模型 '{model_name}' 配置未找到")

        model_mode = model_config.get("mode", "Chat")

        # 获取接口插件
        interface_plugin = self.model_controller.plugin_manager.get_interface_plugin(model_mode)
        if not interface_plugin:
            raise HTTPException(status_code=400, detail=f"不支持的模型模式: {model_mode}")

        # 使用插件进行请求验证
        is_valid, error_message = interface_plugin.validate_request(path, model_name)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_message)

        # 增加待处理请求计数
        self.increment_pending_requests(model_name)

        try:
            # 启动模型（如果未运行）
            success, message = await asyncio.to_thread(
                self.model_controller.start_model, model_name
            )
            if not success:
                raise HTTPException(status_code=503, detail=message)

            # 代理请求到下游模型
            target_port = model_config['port']
            client = await self.get_async_client(target_port)

            # 构建目标URL
            target_url = client.base_url.join(path)

            # 过滤请求头
            headers = dict(request.headers)
            headers.pop("host", None)
            headers.pop("content-length", None)
            headers.pop("transfer-encoding", None)

            # 构建请求
            req = client.build_request(
                request.method,
                target_url,
                headers=headers,
                content=request_data,
                params=request.query_params
            )

            # 发送请求
            response = await client.send(req, stream=True)

            # 处理流式响应
            is_streaming = "text/event-stream" in response.headers.get("content-type", "")

            if is_streaming:
                return StreamingResponse(
                    token_tracker.create_stream_with_token_logging(model_name, response),
                    status_code=response.status_code,
                    headers=dict(response.headers)
                )
            else:
                # 处理非流式响应
                logger.debug(f"[API_ROUTER] 开始处理非流式响应 - 模型: {model_name}")
                content = await response.aread()
                await response.aclose()
                self.mark_request_completed(model_name)
                logger.debug(f"[API_ROUTER] 非流式响应读取完成 - 模型: {model_name}, 响应大小: {len(content)} bytes")

                # 提取token信息并异步记录
                try:
                    input_tokens, output_tokens = token_tracker.extract_tokens_from_response(content)
                    if input_tokens > 0 or output_tokens > 0:
                        logger.debug(f"[API_ROUTER] 准备异步记录非流式响应token - 模型: {model_name}, 总token数: {input_tokens + output_tokens}")
                        await token_tracker.record_request_tokens(model_name, input_tokens, output_tokens)
                    else:
                        logger.debug(f"[API_ROUTER] 跳过非流式响应token记录 - 模型: {model_name}, token数为0")
                except Exception as e:
                    logger.error(f"[API_ROUTER] 非流式响应token记录失败 - 模型: {model_name}, 错误: {e}")
                    # 不抛出异常，避免影响主要请求流程

                return Response(
                    content=content,
                    status_code=response.status_code,
                    headers=dict(response.headers)
                )

        except Exception as e:
            # 统一的异常处理
            logger.error(f"处理对 '{model_name}' 的请求时出错: {e}", exc_info=True)
            self.mark_request_completed(model_name)

            if isinstance(e, HTTPException):
                raise e

            # 对于其他未知异常，包装成500错误
            raise HTTPException(status_code=500, detail=f"内部服务器错误: {str(e)}")


class APIServer:
    """API服务器 - 负责FastAPI应用管理和路由配置"""

    def __init__(self, config_manager: ConfigManager):
        self.config_manager = config_manager
        self.model_controller = ModelController(self.config_manager)
        self.monitor = Monitor()
        self.token_tracker = TokenTracker(self.monitor)
        self.api_router = APIRouter(self.config_manager, self.model_controller)

        # 初始化FastAPI应用
        self.app = FastAPI(title="LLM-Manager API", version="1.0.0")

        # 设置路由
        self._setup_routes()

        # 自动启动标记为auto_start的模型
        self.model_controller.start_auto_start_models()

        logger.info("API服务器初始化完成")
        logger.debug("[API_SERVER] token跟踪功能已激活")
  
    def _setup_routes(self):
        """设置基础路由"""

        @self.app.get("/v1/models", response_class=JSONResponse)
        async def list_models():
            """获取模型列表接口"""
            return self.model_controller.get_model_list()

        @self.app.get("/")
        async def root():
            """根路径"""
            return {
                "message": "LLM-Manager API Server",
                "version": "1.0.0",
                "models_url": "/v1/models"
            }

        @self.app.get("/health")
        async def health_check():
            """健康检查接口"""
            return {
                "status": "healthy",
                "models_count": len(self.model_controller.models_state),
                "running_models": len([
                    s for s in self.model_controller.models_state.values()
                    if s['status'] == 'routing'
                ])
            }

        @self.app.post("/api/models/{model_alias}/start")
        async def start_model_api(model_alias: str):
            """启动模型API - 在入口处解析别名"""
            try:
                model_name = self.config_manager.resolve_primary_name(model_alias)
                success, message = await asyncio.to_thread(
                    self.model_controller.start_model, model_name
                )
                return {"success": success, "message": message}
            except KeyError as e:
                return {"success": False, "message": f"模型别名 '{model_alias}' 未找到"}
            except Exception as e:
                return {"success": False, "message": str(e)}

        @self.app.post("/api/models/{model_alias}/stop")
        async def stop_model_api(model_alias: str):
            """停止模型API - 在入口处解析别名"""
            try:
                model_name = self.config_manager.resolve_primary_name(model_alias)
                success, message = await asyncio.to_thread(
                    self.model_controller.stop_model, model_name
                )
                return {"success": success, "message": message}
            except KeyError as e:
                return {"success": False, "message": f"模型别名 '{model_alias}' 未找到"}
            except Exception as e:
                return {"success": False, "message": str(e)}

        @self.app.get("/api/models/{model_alias}/logs")
        async def get_model_logs(model_alias: str):
            """获取模型日志API - 在入口处解析别名"""
            try:
                model_name = self.config_manager.resolve_primary_name(model_alias)
                logs = self.model_controller.get_model_logs(model_name)
                return logs
            except KeyError as e:
                return [{"timestamp": time.time(), "level": "error", "message": f"模型别名 '{model_alias}' 未找到"}]
            except Exception as e:
                return []

        @self.app.post("/api/models/restart-autostart")
        async def restart_autostart_models():
            """重启所有autostart模型"""
            try:
                logger.info("[API_SERVER] 通过API重启所有autostart模型...")

                # 先卸载所有模型
                await asyncio.to_thread(self.model_controller.unload_all_models)

                # 等待一下
                await asyncio.sleep(2)

                # 启动所有autostart模型
                started_models = []
                for model_name in self.config_manager.get_model_names():
                    if self.config_manager.is_auto_start(model_name):
                        success, message = await asyncio.to_thread(
                            self.model_controller.start_model, model_name
                        )
                        if success:
                            started_models.append(model_name)
                        else:
                            logger.warning(f"[API_SERVER] 自动启动模型 {model_name} 失败: {message}")

                return {
                    "success": True,
                    "message": f"已重启 {len(started_models)} 个autostart模型",
                    "started_models": started_models
                }
            except Exception as e:
                logger.error(f"[API_SERVER] 重启autostart模型失败: {e}")
                return {"success": False, "message": str(e)}

        @self.app.post("/api/models/stop-all")
        async def stop_all_models():
            """关闭所有模型"""
            try:
                logger.info("[API_SERVER] 通过API关闭所有模型...")
                await asyncio.to_thread(self.model_controller.unload_all_models)
                return {
                    "success": True,
                    "message": "所有模型已关闭"
                }
            except Exception as e:
                logger.error(f"[API_SERVER] 关闭所有模型失败: {e}")
                return {"success": False, "message": str(e)}

        @self.app.get("/api/devices/info")
        async def get_device_info():
            """获取设备信息"""
            try:
                devices_info = {}

                for device_name, device_plugin in self.model_controller.plugin_manager.get_all_device_plugins().items():
                    try:
                        device_info = device_plugin.get_devices_info()
                        devices_info[device_name] = {
                            "online": device_plugin.is_online(),
                            "info": device_info
                        }
                    except Exception as e:
                        logger.error(f"[API_SERVER] 获取设备 {device_name} 信息失败: {e}")
                        devices_info[device_name] = {
                            "online": False,
                            "error": str(e)
                        }

                return {
                    "success": True,
                    "devices": devices_info
                }
            except Exception as e:
                logger.error(f"[API_SERVER] 获取设备信息失败: {e}")
                return {"success": False, "message": str(e)}

        @self.app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
        async def handle_all_requests(request: Request, path: str):
            """统一请求处理器"""
            return await self.api_router.route_request(request, path, self.token_tracker)

    
    def run(self, host: Optional[str] = None, port: Optional[int] = None):
        """运行API服务器"""
        import uvicorn

        # 如果没有指定主机和端口，从配置中获取
        if host is None or port is None:
            api_cfg = self.config_manager.get_openai_config()
            host = host or api_cfg['host']
            port = port or api_cfg['port']

        logger.info(f"[API_SERVER] 统一API接口将在 http://{host}:{port} 上启动")
        uvicorn.run(self.app, host=host, port=port, log_level="warning")

    def run(self, host: Optional[str] = None, port: Optional[int] = None):
        """运行API服务器"""
        import uvicorn

        # 如果没有指定主机和端口，从配置中获取
        if host is None or port is None:
            api_cfg = self.config_manager.get_openai_config()
            host = host or api_cfg['host']
            port = port or api_cfg['port']

        logger.info(f"[API_SERVER] 统一API接口将在 http://{host}:{port} 上启动")
        uvicorn.run(self.app, host=host, port=port, log_level="warning")


# 全局变量
_app_instance: Optional[FastAPI] = None
_server_instance: Optional[APIServer] = None

def run_api_server(config_manager: ConfigManager, host: Optional[str] = None, port: Optional[int] = None):
    """运行API服务器"""
    global _app_instance, _server_instance
    _server_instance = APIServer(config_manager)
    _app_instance = _server_instance.app
    _server_instance.run(host, port)