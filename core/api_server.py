from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import StreamingResponse, JSONResponse
from typing import Dict, List, Optional, Any
import json
import time
import asyncio
import queue
import threading
from utils.logger import get_logger
from core.config_manager import ConfigManager
from core.model_controller import ModelController
from core.data_manager import Monitor
from core.api_router import APIRouter, TokenTracker

logger = get_logger(__name__)


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


        @self.app.get("/api/models/{model_alias}/logs/stream")
        async def stream_model_logs(model_alias: str):
            """流式获取模型控制台日志API - 实时推送模型控制台输出"""
            try:
                # 解析模型名称
                model_name = self.config_manager.resolve_primary_name(model_alias)

                # 检查模型是否存在
                if model_name not in self.model_controller.models_state:
                    return JSONResponse(
                        status_code=404,
                        content={"error": f"模型 '{model_alias}' 不存在"}
                    )

                # 检查模型是否启动
                model_status = self.model_controller.models_state[model_name].get('status')
                if model_status not in ['routing', 'starting', 'init_script', 'health_check']:
                    return JSONResponse(
                        status_code=400,
                        content={"error": f"模型 '{model_alias}' 未启动或已停止 (当前状态: {model_status})"}
                    )

                # 获取历史日志
                historical_logs = self.model_controller.get_model_logs(model_name)

                async def log_stream_generator():
                    # 首先发送历史日志
                    for log_entry in historical_logs:
                        data = {
                            "type": "historical",
                            "log": log_entry
                        }
                        yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
                        await asyncio.sleep(0.01)  # 避免发送过快

                    # 发送历史日志结束标记
                    yield f"data: {json.dumps({'type': 'historical_complete'}, ensure_ascii=False)}\n\n"

                    # 订阅实时日志
                    subscriber_queue = self.model_controller.subscribe_to_model_logs(model_name)

                    try:
                        while True:
                            try:
                                # 使用非阻塞方式获取日志
                                log_entry = await asyncio.to_thread(subscriber_queue.get, timeout=1.0)

                                if log_entry is None:
                                    # 收到结束信号
                                    yield f"data: {json.dumps({'type': 'stream_end'}, ensure_ascii=False)}\n\n"
                                    break

                                # 发送实时日志
                                data = {
                                    "type": "realtime",
                                    "log": log_entry
                                }
                                yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"

                            except queue.Empty:
                                # 超时，检查连接是否仍然活跃
                                continue
                            except Exception as e:
                                logger.error(f"流式日志推送错误: {e}")
                                yield f"data: {json.dumps({'type': 'error', 'message': str(e)}, ensure_ascii=False)}\n\n"
                                break

                    finally:
                        # 取消订阅
                        self.model_controller.unsubscribe_from_model_logs(model_name, subscriber_queue)

                return StreamingResponse(
                    log_stream_generator(),
                    media_type="text/event-stream",
                    headers={
                        "Cache-Control": "no-cache",
                        "Connection": "keep-alive",
                        "Access-Control-Allow-Origin": "*",
                        "Access-Control-Allow-Headers": "*"
                    }
                )

            except KeyError:
                return JSONResponse(
                    status_code=404,
                    content={"error": f"模型别名 '{model_alias}' 未找到"}
                )
            except Exception as e:
                logger.error(f"流式日志接口错误: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"error": f"服务器内部错误: {str(e)}"}
                )

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

        @self.app.get("/api/logs/stats")
        async def get_log_stats():
            """获取模型控制台日志统计信息"""
            try:
                stats = self.model_controller.get_log_stats()
                return {
                    "success": True,
                    "stats": stats
                }
            except Exception as e:
                logger.error(f"[API_SERVER] 获取日志统计失败: {e}")
                return {"success": False, "message": str(e)}

        @self.app.post("/api/logs/{model_alias}/clear")
        async def clear_model_logs(model_alias: str, keep_minutes: int = 0):
            """清理模型控制台日志 - 保留最近N分钟的日志

            Args:
                model_alias: 模型别名
                keep_minutes: 保留最近多少分钟的日志 (0表示清空所有)
            """
            try:
                model_name = self.config_manager.resolve_primary_name(model_alias)

                if keep_minutes == 0:
                    # 清空所有日志
                    self.model_controller.log_manager.clear_logs(model_name)
                    message = f"模型 '{model_alias}' 所有日志已清空"
                else:
                    # 清理指定分钟数之前的日志
                    removed_count = self.model_controller.log_manager.cleanup_old_logs(model_name, keep_minutes)
                    message = f"模型 '{model_alias}' 已清理 {keep_minutes} 分钟前的日志，删除 {removed_count} 条"

                return {"success": True, "message": message}
            except KeyError as e:
                return {"success": False, "message": f"模型别名 '{model_alias}' 未找到"}
            except Exception as e:
                logger.error(f"[API_SERVER] 清理日志失败: {e}")
                return {"success": False, "message": str(e)}

        @self.app.get("/api/models/{model_alias}/info")
        async def get_model_info(model_alias: str):
            """获取模型信息 - 包括启停状态和待处理请求数

            Args:
                model_alias: 模型别名或"all-models"获取全部模型信息
            """
            try:
                # 处理all-models特殊情况
                if model_alias == "all-models":
                    all_models_info = {}

                    # 获取所有模型的状态
                    all_models_status = self.model_controller.get_all_models_status()

                    for model_name, model_status in all_models_status.items():
                        # 获取待处理请求数
                        pending_requests = self.api_router.pending_requests.get(model_name, 0)

                        # 构建模型信息
                        model_info = {
                            "model_name": model_name,
                            "aliases": model_status.get("aliases", [model_name]),
                            "status": model_status.get("status", "unknown"),
                            "pid": model_status.get("pid"),
                            "idle_time_sec": model_status.get("idle_time_sec", "N/A"),
                            "mode": model_status.get("mode", "Chat"),
                            "is_available": model_status.get("is_available", False),
                            "current_bat_path": model_status.get("current_bat_path", ""),
                            "config_source": model_status.get("config_source", "N/A"),
                            "failure_reason": model_status.get("failure_reason"),
                            "pending_requests": pending_requests
                        }

                        all_models_info[model_name] = model_info

                    return {
                        "success": True,
                        "models": all_models_info,
                        "total_models": len(all_models_info),
                        "running_models": len([
                            m for m in all_models_info.values()
                            if m["status"] == "routing"
                        ]),
                        "total_pending_requests": sum(m["pending_requests"] for m in all_models_info.values())
                    }

                # 处理单个模型
                else:
                    # 解析模型名称
                    model_name = self.config_manager.resolve_primary_name(model_alias)

                    # 检查模型是否存在
                    if model_name not in self.model_controller.models_state:
                        return JSONResponse(
                            status_code=404,
                            content={"success": False, "error": f"模型 '{model_alias}' 不存在"}
                        )

                    # 获取模型状态
                    all_models_status = self.model_controller.get_all_models_status()
                    model_status = all_models_status.get(model_name)

                    if not model_status:
                        return JSONResponse(
                            status_code=404,
                            content={"success": False, "error": f"模型 '{model_alias}' 状态信息未找到"}
                        )

                    # 获取待处理请求数
                    pending_requests = self.api_router.pending_requests.get(model_name, 0)

                    # 构建模型信息
                    model_info = {
                        "model_name": model_name,
                        "aliases": model_status.get("aliases", [model_name]),
                        "status": model_status.get("status", "unknown"),
                        "pid": model_status.get("pid"),
                        "idle_time_sec": model_status.get("idle_time_sec", "N/A"),
                        "mode": model_status.get("mode", "Chat"),
                        "is_available": model_status.get("is_available", False),
                        "current_bat_path": model_status.get("current_bat_path", ""),
                        "config_source": model_status.get("config_source", "N/A"),
                        "failure_reason": model_status.get("failure_reason"),
                        "pending_requests": pending_requests
                    }

                    return {
                        "success": True,
                        "model": model_info
                    }

            except KeyError:
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "error": f"模型别名 '{model_alias}' 未找到"}
                )
            except Exception as e:
                logger.error(f"[API_SERVER] 获取模型信息失败: {e}")
                return JSONResponse(
                    status_code=500,
                    content={"success": False, "error": f"服务器内部错误: {str(e)}"}
                )



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


# 全局变量
_app_instance: Optional[FastAPI] = None
_server_instance: Optional[APIServer] = None

def run_api_server(config_manager: ConfigManager, host: Optional[str] = None, port: Optional[int] = None):
    """运行API服务器"""
    global _app_instance, _server_instance
    _server_instance = APIServer(config_manager)
    _app_instance = _server_instance.app
    _server_instance.run(host, port)