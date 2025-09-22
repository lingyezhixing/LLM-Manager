from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import StreamingResponse, JSONResponse
from typing import Dict, List, Optional
import logging
import json
import asyncio
from utils.logger import get_logger
from core.model_controller import ModelController
from core.config_manager import ConfigManager

logger = get_logger(__name__)

class APIServer:
    """API服务器 - 提供统一的OpenAI API接口"""

    def __init__(self, model_controller: ModelController):
        self.model_controller = model_controller
        self.config_manager = model_controller.config_manager
        self.app = FastAPI(title="LLM-Manager API", version="1.0.0")
        self.async_clients: Dict[int, any] = {}
        self._setup_routes()

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
            """启动模型API"""
            try:
                success, message = await asyncio.to_thread(
                    self.model_controller.start_model, model_alias
                )
                return {"success": success, "message": message}
            except Exception as e:
                return {"success": False, "message": str(e)}

        @self.app.post("/api/models/{model_alias}/stop")
        async def stop_model_api(model_alias: str):
            """停止模型API"""
            try:
                success, message = await asyncio.to_thread(
                    self.model_controller.stop_model, model_alias
                )
                return {"success": success, "message": message}
            except Exception as e:
                return {"success": False, "message": str(e)}

        @self.app.get("/api/models/{model_alias}/logs")
        async def get_model_logs(model_alias: str):
            """获取模型日志API"""
            try:
                logs = self.model_controller.get_model_logs(model_alias)
                return logs
            except Exception as e:
                return []

        @self.app.post("/api/models/restart-autostart")
        async def restart_autostart_models():
            """重启所有autostart模型"""
            try:
                logger.info("通过API重启所有autostart模型...")

                # 先卸载所有模型
                await asyncio.to_thread(self.model_controller.unload_all_models)

                # 等待一下
                await asyncio.sleep(2)

                # 启动所有autostart模型
                started_models = []
                for primary_name in self.config_manager.get_model_names():
                    if self.config_manager.is_auto_start(primary_name):
                        success, message = await asyncio.to_thread(
                            self.model_controller.start_model, primary_name
                        )
                        if success:
                            started_models.append(primary_name)
                        else:
                            logger.warning(f"自动启动模型 {primary_name} 失败: {message}")

                return {
                    "success": True,
                    "message": f"已重启 {len(started_models)} 个autostart模型",
                    "started_models": started_models
                }
            except Exception as e:
                logger.error(f"重启autostart模型失败: {e}")
                return {"success": False, "message": str(e)}

        @self.app.post("/api/models/stop-all")
        async def stop_all_models():
            """关闭所有模型"""
            try:
                logger.info("通过API关闭所有模型...")
                await asyncio.to_thread(self.model_controller.unload_all_models)
                return {
                    "success": True,
                    "message": "所有模型已关闭"
                }
            except Exception as e:
                logger.error(f"关闭所有模型失败: {e}")
                return {"success": False, "message": str(e)}

        @self.app.get("/api/devices/info")
        async def get_device_info():
            """获取设备信息"""
            try:
                devices_info = {}

                for device_name, device_plugin in self.model_controller.device_plugins.items():
                    try:
                        device_info = device_plugin.get_devices_info()
                        devices_info[device_name] = {
                            "online": device_plugin.is_online(),
                            "info": device_info
                        }
                    except Exception as e:
                        logger.error(f"获取设备 {device_name} 信息失败: {e}")
                        devices_info[device_name] = {
                            "online": False,
                            "error": str(e)
                        }

                return {
                    "success": True,
                    "devices": devices_info
                }
            except Exception as e:
                logger.error(f"获取设备信息失败: {e}")
                return {"success": False, "message": str(e)}

        @self.app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
        async def handle_all_requests(request: Request, path: str):
            """统一请求处理器"""
            return await self.handle_request(request, path)

    
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

    async def stream_proxy_wrapper(self, model_alias: str, response: any):
        """包装流式响应，以在结束后更新请求计数器"""
        try:
            async for chunk in response.aiter_bytes():
                yield chunk
        finally:
            await response.aclose()
            self.model_controller.mark_request_completed(model_alias)

    def create_error_response(self, detail: str, status_code: int = 500) -> JSONResponse:
        """创建错误响应"""
        return JSONResponse(
            status_code=status_code,
            content={"error": True, "message": detail}
        )

    async def handle_request(self, request: Request, path: str) -> Response:
        """统一请求处理器"""
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

        # 检查模型别名是否存在
        try:
            self.model_controller.resolve_primary_name(model_alias)
        except KeyError:
            raise HTTPException(status_code=404, detail=f"模型别名 '{model_alias}' 未在配置中找到")

        # 获取模型配置并验证模式
        model_config = self.model_controller.get_model_config(model_alias)
        if not model_config:
            raise HTTPException(status_code=404, detail=f"模型 '{model_alias}' 配置未找到")

        model_mode = model_config.get("mode", "Chat")

        # 获取接口插件
        interface_plugin = self.model_controller.interface_plugins.get(model_mode)
        if not interface_plugin:
            raise HTTPException(status_code=400, detail=f"不支持的模型模式: {model_mode}")

        # 使用插件进行请求验证
        is_valid, error_message = interface_plugin.validate_request(path, model_alias)
        if not is_valid:
            raise HTTPException(status_code=400, detail=error_message)

        # 增加待处理请求计数
        self.model_controller.increment_pending_requests(model_alias)

        try:
            # 启动模型（如果未运行）
            success, message = await asyncio.to_thread(
                self.model_controller.start_model, model_alias
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
                    self.stream_proxy_wrapper(model_alias, response),
                    status_code=response.status_code,
                    headers=dict(response.headers)
                )
            else:
                # 处理非流式响应
                content = await response.aread()
                await response.aclose()
                self.model_controller.mark_request_completed(model_alias)

                return Response(
                    content=content,
                    status_code=response.status_code,
                    headers=dict(response.headers)
                )

        except Exception as e:
            # 统一的异常处理
            logger.error(f"处理对 '{model_alias}' 的请求时出错: {e}", exc_info=True)
            self.model_controller.mark_request_completed(model_alias)

            if isinstance(e, HTTPException):
                raise e

            # 对于其他未知异常，包装成500错误
            raise HTTPException(status_code=500, detail=f"内部服务器错误: {str(e)}")

    def run(self, host: str, port: int):
        """运行API服务器"""
        import uvicorn
        logger.info(f"统一API接口将在 http://{host}:{port} 上启动")
        uvicorn.run(self.app, host=host, port=port, log_level="warning")

# 全局变量
app: Optional[FastAPI] = None
api_server: Optional[APIServer] = None

def initialize_api_server(model_controller: ModelController) -> APIServer:
    """初始化API服务器"""
    global app, api_server
    api_server = APIServer(model_controller)
    app = api_server.app
    return api_server

def run_api_server(model_controller: ModelController, host: str, port: int):
    """运行API服务器的便捷函数"""
    server = initialize_api_server(model_controller)
    server.run(host, port)