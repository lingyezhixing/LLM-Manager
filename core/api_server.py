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

        @self.app.get("/api/metrics/throughput/realtime")
        async def get_realtime_throughput():
            """获取实时吞吐量数据（最近5秒）"""
            try:
                import time
                current_time = time.time()
                five_seconds_ago = current_time - 5

                total_input_tokens = 0
                total_output_tokens = 0
                total_cache_n = 0
                total_prompt_n = 0

                # 遍历所有模型，获取最近5秒的请求数据
                for model_name in self.config_manager.get_model_names():
                    requests = self.monitor.get_model_requests(model_name, minutes=0)  # 获取所有请求
                    recent_requests = [
                        req for req in requests
                        if req.timestamp >= five_seconds_ago
                    ]

                    for req in recent_requests:
                        total_input_tokens += req.input_tokens
                        total_output_tokens += req.output_tokens
                        total_cache_n += req.cache_n
                        total_prompt_n += req.prompt_n

                # 计算每秒吞吐量
                time_window = max(5, current_time - five_seconds_ago)  # 避免除零

                throughput_data = {
                    "throughput": {
                        "input_tokens_per_sec": total_input_tokens / time_window,
                        "output_tokens_per_sec": total_output_tokens / time_window,
                        "total_tokens_per_sec": (total_input_tokens + total_output_tokens) / time_window,
                        "cache_hit_tokens_per_sec": total_cache_n / time_window,
                        "cache_miss_tokens_per_sec": total_prompt_n / time_window
                    }
                }

                return {
                    "success": True,
                    "data": throughput_data
                }

            except Exception as e:
                logger.error(f"[API_SERVER] 获取实时吞吐量失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.get("/api/metrics/throughput/current-session")
        async def get_current_session_total():
            """获取本次运行总消耗"""
            try:
                # 获取程序启动时间
                program_runtime = self.monitor.get_program_runtime(limit=1)
                if not program_runtime:
                    # 如果没有启动时间记录，返回默认值
                    return {
                        "success": True,
                        "data": {
                            "session_total": {
                                "total_cost_yuan": 0.0,
                                "total_input_tokens": 0,
                                "total_output_tokens": 0,
                                "total_cache_n": 0,
                                "total_prompt_n": 0,
                                "session_start_time": None
                            }
                        }
                    }

                start_time = program_runtime[0].start_time
                total_input_tokens = 0
                total_output_tokens = 0
                total_cache_n = 0
                total_prompt_n = 0
                total_cost = 0.0

                # 遍历所有模型，获取本次运行的所有请求数据
                for model_name in self.config_manager.get_model_names():
                    requests = self.monitor.get_model_requests(model_name, minutes=0)
                    billing = self.monitor.get_model_billing(model_name)

                    for req in requests:
                        if req.timestamp >= start_time:
                            total_input_tokens += req.input_tokens
                            total_output_tokens += req.output_tokens
                            total_cache_n += req.cache_n
                            total_prompt_n += req.prompt_n

                            # 计算成本
                            if billing:
                                if billing.use_tier_pricing and billing.tier_pricing:
                                    # 阶梯计费
                                    total_tokens = req.input_tokens + req.output_tokens
                                    cost = 0.0
                                    remaining_tokens = total_tokens

                                    for tier in billing.tier_pricing:
                                        if remaining_tokens <= 0:
                                            break

                                        tier_tokens = min(remaining_tokens, tier.end_tokens - tier.start_tokens)
                                        input_cost = (req.input_tokens * tier.input_price_per_million) / 1000000
                                        output_cost = (req.output_tokens * tier.output_price_per_million) / 1000000
                                        cost += input_cost + output_cost
                                        remaining_tokens -= tier_tokens

                                    total_cost += cost
                                else:
                                    # 按时计费（这里简化处理，实际需要运行时间）
                                    total_cost += billing.hourly_price * 0.001  # 简化计算

                return {
                    "success": True,
                    "data": {
                        "session_total": {
                            "total_cost_yuan": round(total_cost, 6),
                            "total_input_tokens": total_input_tokens,
                            "total_output_tokens": total_output_tokens,
                            "total_cache_n": total_cache_n,
                            "total_prompt_n": total_prompt_n,
                            "session_start_time": start_time
                        }
                    }
                }

            except Exception as e:
                logger.error(f"[API_SERVER] 获取本次运行总消耗失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.get("/api/analytics/token-distribution/{time_range}")
        async def get_token_distribution(time_range: str):
            """获取Token分布比例数据"""
            try:
                # 解析时间范围
                time_mapping = {
                    "10min": 10, "30min": 30, "1h": 60, "12h": 720,
                    "1d": 1440, "1w": 10080, "1m": 43200,
                    "3m": 129600, "6m": 259200, "1y": 525600, "all": 0
                }

                minutes = time_mapping.get(time_range)
                if minutes is None:
                    return {"success": False, "error": "无效的时间范围参数"}

                import time
                end_time = time.time()
                start_time = end_time - (minutes * 60) if minutes > 0 else 0

                model_token_data = {}

                for model_name in self.config_manager.get_model_names():
                    requests = self.monitor.get_model_requests(model_name, minutes=minutes)

                    # 过滤时间范围内的请求
                    time_filtered_requests = [
                        req for req in requests
                        if req.timestamp >= start_time
                    ]

                    if time_filtered_requests:
                        total_tokens = sum(req.input_tokens + req.output_tokens for req in time_filtered_requests)
                        model_token_data[model_name] = total_tokens

                # 按时间分段聚合数据（每10分钟一个数据点）
                time_points = []
                current_time = start_time
                point_interval = max(600, minutes * 60 / 100)  # 至少10分钟一个点，最多100个点

                while current_time < end_time:
                    point_end_time = min(current_time + point_interval, end_time)

                    point_data = {}
                    for model_name in self.config_manager.get_model_names():
                        requests = self.monitor.get_model_requests(model_name, minutes=minutes)
                        point_requests = [
                            req for req in requests
                            if current_time <= req.timestamp < point_end_time
                        ]

                        if point_requests:
                            total_tokens = sum(req.input_tokens + req.output_tokens for req in point_requests)
                            point_data[model_name] = total_tokens

                    time_points.append({
                        "timestamp": current_time,
                        "data": point_data
                    })

                    current_time = point_end_time

                return {
                    "success": True,
                    "data": {
                        "time_range": time_range,
                        "time_points": time_points,
                        "model_token_data": model_token_data
                    }
                }

            except Exception as e:
                logger.error(f"[API_SERVER] 获取Token分布失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.get("/api/analytics/token-trends/{time_range}")
        async def get_token_trends(time_range: str):
            """获取Token消耗趋势数据"""
            try:
                # 解析时间范围
                time_mapping = {
                    "10min": 10, "30min": 30, "1h": 60, "12h": 720,
                    "1d": 1440, "1w": 10080, "1m": 43200,
                    "3m": 129600, "6m": 259200, "1y": 525600, "all": 0
                }

                minutes = time_mapping.get(time_range)
                if minutes is None:
                    return {"success": False, "error": "无效的时间范围参数"}

                import time
                end_time = time.time()
                start_time = end_time - (minutes * 60) if minutes > 0 else 0

                # 按时间分段聚合数据
                time_points = []
                point_interval = max(300, minutes * 60 / 50)  # 至少5分钟一个点，最多50个点

                current_time = start_time
                while current_time < end_time:
                    point_end_time = min(current_time + point_interval, end_time)

                    point_data = {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                        "cache_hit_tokens": 0,
                        "cache_miss_tokens": 0
                    }

                    for model_name in self.config_manager.get_model_names():
                        requests = self.monitor.get_model_requests(model_name, minutes=minutes)
                        point_requests = [
                            req for req in requests
                            if current_time <= req.timestamp < point_end_time
                        ]

                        for req in point_requests:
                            point_data["input_tokens"] += req.input_tokens
                            point_data["output_tokens"] += req.output_tokens
                            point_data["total_tokens"] += req.input_tokens + req.output_tokens
                            point_data["cache_hit_tokens"] += req.cache_n
                            point_data["cache_miss_tokens"] += req.prompt_n

                    time_points.append({
                        "timestamp": current_time,
                        "data": point_data
                    })

                    current_time = point_end_time

                return {
                    "success": True,
                    "data": {
                        "time_range": time_range,
                        "time_points": time_points
                    }
                }

            except Exception as e:
                logger.error(f"[API_SERVER] 获取Token趋势失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.get("/api/analytics/cost-trends/{time_range}")
        async def get_cost_trends(time_range: str):
            """获取成本趋势数据"""
            try:
                # 解析时间范围
                time_mapping = {
                    "10min": 10, "30min": 30, "1h": 60, "12h": 720,
                    "1d": 1440, "1w": 10080, "1m": 43200,
                    "3m": 129600, "6m": 259200, "1y": 525600, "all": 0
                }

                minutes = time_mapping.get(time_range)
                if minutes is None:
                    return {"success": False, "error": "无效的时间范围参数"}

                import time
                end_time = time.time()
                start_time = end_time - (minutes * 60) if minutes > 0 else 0

                # 按时间分段聚合数据
                time_points = []
                point_interval = max(300, minutes * 60 / 50)  # 至少5分钟一个点，最多50个点

                current_time = start_time
                while current_time < end_time:
                    point_end_time = min(current_time + point_interval, end_time)

                    point_cost = 0.0

                    for model_name in self.config_manager.get_model_names():
                        requests = self.monitor.get_model_requests(model_name, minutes=minutes)
                        billing = self.monitor.get_model_billing(model_name)

                        point_requests = [
                            req for req in requests
                            if current_time <= req.timestamp < point_end_time
                        ]

                        if billing and point_requests:
                            if billing.use_tier_pricing and billing.tier_pricing:
                                for req in point_requests:
                                    total_tokens = req.input_tokens + req.output_tokens
                                    cost = 0.0
                                    remaining_tokens = total_tokens

                                    for tier in billing.tier_pricing:
                                        if remaining_tokens <= 0:
                                            break

                                        tier_tokens = min(remaining_tokens, tier.end_tokens - tier.start_tokens)
                                        input_cost = (req.input_tokens * tier.input_price_per_million) / 1000000
                                        output_cost = (req.output_tokens * tier.output_price_per_million) / 1000000
                                        cost += input_cost + output_cost
                                        remaining_tokens -= tier_tokens

                                    point_cost += cost
                            else:
                                # 按时计费（简化处理）
                                point_cost += billing.hourly_price * (point_interval / 3600)

                    time_points.append({
                        "timestamp": current_time,
                        "cost": point_cost
                    })

                    current_time = point_end_time

                return {
                    "success": True,
                    "data": {
                        "time_range": time_range,
                        "time_points": time_points
                    }
                }

            except Exception as e:
                logger.error(f"[API_SERVER] 获取成本趋势失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.get("/api/analytics/model-stats/{model_name}/{time_range}")
        async def get_model_stats(model_name: str, time_range: str):
            """获取单模型统计数据"""
            try:
                # 解析时间范围
                time_mapping = {
                    "10min": 10, "30min": 30, "1h": 60, "12h": 720,
                    "1d": 1440, "1w": 10080, "1m": 43200,
                    "3m": 129600, "6m": 259200, "1y": 525600, "all": 0
                }

                minutes = time_mapping.get(time_range)
                if minutes is None:
                    return {"success": False, "error": "无效的时间范围参数"}

                import time
                end_time = time.time()
                start_time = end_time - (minutes * 60) if minutes > 0 else 0

                # 获取模型请求数据
                requests = self.monitor.get_model_requests(model_name, minutes=minutes)
                time_filtered_requests = [
                    req for req in requests
                    if req.timestamp >= start_time
                ]

                # 计算统计数据
                total_input_tokens = sum(req.input_tokens for req in time_filtered_requests)
                total_output_tokens = sum(req.output_tokens for req in time_filtered_requests)
                total_cache_n = sum(req.cache_n for req in time_filtered_requests)
                total_prompt_n = sum(req.prompt_n for req in time_filtered_requests)

                # 计算成本
                billing = self.monitor.get_model_billing(model_name)
                total_cost = 0.0

                if billing and time_filtered_requests:
                    if billing.use_tier_pricing and billing.tier_pricing:
                        for req in time_filtered_requests:
                            total_tokens = req.input_tokens + req.output_tokens
                            cost = 0.0
                            remaining_tokens = total_tokens

                            for tier in billing.tier_pricing:
                                if remaining_tokens <= 0:
                                    break

                                tier_tokens = min(remaining_tokens, tier.end_tokens - tier.start_tokens)
                                input_cost = (req.input_tokens * tier.input_price_per_million) / 1000000
                                output_cost = (req.output_tokens * tier.output_price_per_million) / 1000000
                                cost += input_cost + output_cost
                                remaining_tokens -= tier_tokens

                            total_cost += cost
                    else:
                        # 按时计费（简化处理）
                        total_cost += billing.hourly_price * (minutes / 60)

                # 按时间分段数据
                time_points = []
                point_interval = max(300, minutes * 60 / 50)

                current_time = start_time
                while current_time < end_time:
                    point_end_time = min(current_time + point_interval, end_time)

                    point_data = {
                        "input_tokens": 0,
                        "output_tokens": 0,
                        "total_tokens": 0,
                        "cache_hit_tokens": 0,
                        "cache_miss_tokens": 0,
                        "cost": 0.0
                    }

                    point_requests = [
                        req for req in time_filtered_requests
                        if current_time <= req.timestamp < point_end_time
                    ]

                    for req in point_requests:
                        point_data["input_tokens"] += req.input_tokens
                        point_data["output_tokens"] += req.output_tokens
                        point_data["total_tokens"] += req.input_tokens + req.output_tokens
                        point_data["cache_hit_tokens"] += req.cache_n
                        point_data["cache_miss_tokens"] += req.prompt_n

                    time_points.append({
                        "timestamp": current_time,
                        "data": point_data
                    })

                    current_time = point_end_time

                return {
                    "success": True,
                    "data": {
                        "model_name": model_name,
                        "time_range": time_range,
                        "summary": {
                            "total_input_tokens": total_input_tokens,
                            "total_output_tokens": total_output_tokens,
                            "total_tokens": total_input_tokens + total_output_tokens,
                            "total_cache_n": total_cache_n,
                            "total_prompt_n": total_prompt_n,
                            "total_cost": round(total_cost, 6),
                            "request_count": len(time_filtered_requests)
                        },
                        "time_points": time_points
                    }
                }

            except Exception as e:
                logger.error(f"[API_SERVER] 获取模型统计数据失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.get("/api/billing/models/{model_name}/pricing")
        async def get_model_pricing(model_name: str):
            """获取模型计费配置"""
            try:
                # 解析模型名称
                primary_name = self.config_manager.resolve_primary_name(model_name)

                billing = self.monitor.get_model_billing(primary_name)
                if not billing:
                    return {"success": False, "error": f"模型 '{model_name}' 的计费配置不存在"}

                return {
                    "success": True,
                    "data": {
                        "model_name": primary_name,
                        "pricing_type": "tier" if billing.use_tier_pricing else "hourly",
                        "tier_pricing": [
                            {
                                "tier_index": tier.tier_index,
                                "start_tokens": tier.start_tokens,
                                "end_tokens": tier.end_tokens,
                                "input_price_per_million": tier.input_price_per_million,
                                "output_price_per_million": tier.output_price_per_million,
                                "support_cache": tier.support_cache,
                                "cache_hit_price_per_million": tier.cache_hit_price_per_million
                            }
                            for tier in billing.tier_pricing
                        ],
                        "hourly_price": billing.hourly_price
                    }
                }

            except KeyError:
                return {"success": False, "error": f"模型 '{model_name}' 不存在"}
            except Exception as e:
                logger.error(f"[API_SERVER] 获取模型计费配置失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.post("/api/billing/models/{model_name}/pricing/tier")
        async def set_tier_pricing(model_name: str, pricing_data: dict):
            """设置模型阶梯计费"""
            try:
                # 解析模型名称
                primary_name = self.config_manager.resolve_primary_name(model_name)

                # 验证数据
                required_fields = ["tier_index", "start_tokens", "end_tokens",
                                 "input_price_per_million", "output_price_per_million",
                                 "support_cache", "cache_hit_price_per_million"]

                for field in required_fields:
                    if field not in pricing_data:
                        return {"success": False, "error": f"缺少必要字段: {field}"}

                # 设置阶梯计费
                tier_data = [
                    pricing_data["tier_index"],
                    pricing_data["start_tokens"],
                    pricing_data["end_tokens"],
                    pricing_data["input_price_per_million"],
                    pricing_data["output_price_per_million"],
                    pricing_data["support_cache"],
                    pricing_data["cache_hit_price_per_million"]
                ]

                self.monitor.update_tier_pricing(primary_name, tier_data)
                self.monitor.update_billing_method(primary_name, True)

                return {
                    "success": True,
                    "message": f"模型 '{model_name}' 阶梯计费配置已更新"
                }

            except KeyError:
                return {"success": False, "error": f"模型 '{model_name}' 不存在"}
            except Exception as e:
                logger.error(f"[API_SERVER] 设置阶梯计费失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.post("/api/billing/models/{model_name}/pricing/hourly")
        async def set_hourly_pricing(model_name: str, pricing_data: dict):
            """设置模型按时计费"""
            try:
                # 解析模型名称
                primary_name = self.config_manager.resolve_primary_name(model_name)

                # 验证数据
                if "hourly_price" not in pricing_data:
                    return {"success": False, "error": "缺少必要字段: hourly_price"}

                # 设置按时计费
                hourly_price = float(pricing_data["hourly_price"])
                self.monitor.update_hourly_price(primary_name, hourly_price)
                self.monitor.update_billing_method(primary_name, False)

                return {
                    "success": True,
                    "message": f"模型 '{model_name}' 按时计费配置已更新"
                }

            except KeyError:
                return {"success": False, "error": f"模型 '{model_name}' 不存在"}
            except Exception as e:
                logger.error(f"[API_SERVER] 设置按时计费失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.get("/api/data/models/orphaned")
        async def get_orphaned_models():
            """获取孤立模型数据（配置中不存在但数据库中有数据的模型）"""
            try:
                import os
                import sqlite3

                # 获取配置中的模型名称
                configured_models = set(self.config_manager.get_model_names())

                # 获取数据库中的所有模型表
                orphaned_models = []

                if os.path.exists(self.monitor.db_path):
                    conn = sqlite3.connect(self.monitor.db_path)
                    cursor = conn.cursor()

                    # 获取所有表名
                    cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                    tables = cursor.fetchall()

                    for table in tables:
                        table_name = table[0]
                        if table_name.endswith('_requests'):
                            # 从表名提取模型名称
                            model_name = table_name[:-9]  # 移除 '_requests' 后缀

                            # 检查是否在映射表中
                            cursor.execute('''
                                SELECT original_name FROM model_name_mapping
                                WHERE safe_name = ?
                            ''', (model_name,))
                            result = cursor.fetchone()

                            if result:
                                original_name = result[0]
                                if original_name not in configured_models:
                                    orphaned_models.append(original_name)

                    conn.close()

                return {
                    "success": True,
                    "data": {
                        "orphaned_models": orphaned_models,
                        "count": len(orphaned_models)
                    }
                }

            except Exception as e:
                logger.error(f"[API_SERVER] 获取孤立模型数据失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.delete("/api/data/models/{model_name}")
        async def delete_model_data(model_name: str):
            """删除指定模型的数据"""
            try:
                # 检查模型是否在配置中
                configured_models = self.config_manager.get_model_names()
                if model_name in configured_models:
                    return {"success": False, "error": f"模型 '{model_name}' 仍在配置中，无法删除"}

                # 删除模型数据
                self.monitor.delete_model_tables(model_name)

                return {
                    "success": True,
                    "message": f"模型 '{model_name}' 的数据已删除"
                }

            except Exception as e:
                logger.error(f"[API_SERVER] 删除模型数据失败: {e}")
                return {"success": False, "error": str(e)}

        @self.app.get("/api/data/storage/stats")
        async def get_storage_stats():
            """获取存储统计信息"""
            try:
                import os
                import sqlite3

                stats = {
                    "database_exists": False,
                    "database_size_mb": 0,
                    "total_models_with_data": 0,
                    "total_requests": 0,
                    "models_data": {}
                }

                if os.path.exists(self.monitor.db_path):
                    stats["database_exists"] = True
                    stats["database_size_mb"] = round(os.path.getsize(self.monitor.db_path) / (1024 * 1024), 2)

                    conn = sqlite3.connect(self.monitor.db_path)
                    cursor = conn.cursor()

                    # 获取配置中的模型名称
                    configured_models = self.config_manager.get_model_names()

                    total_requests = 0

                    for model_name in configured_models:
                        safe_name = self.monitor.get_model_safe_name(model_name)
                        if safe_name:
                            # 获取请求数量
                            cursor.execute(f"SELECT COUNT(*) FROM {safe_name}_requests")
                            request_count = cursor.fetchone()[0]
                            total_requests += request_count

                            stats["models_data"][model_name] = {
                                "request_count": request_count,
                                "has_runtime_data": False,
                                "has_billing_data": False
                            }

                            # 检查是否有运行时间数据
                            cursor.execute(f"SELECT COUNT(*) FROM {safe_name}_runtime")
                            if cursor.fetchone()[0] > 0:
                                stats["models_data"][model_name]["has_runtime_data"] = True

                            # 检查是否有计费数据
                            cursor.execute(f"SELECT COUNT(*) FROM {safe_name}_tier_pricing")
                            if cursor.fetchone()[0] > 0:
                                stats["models_data"][model_name]["has_billing_data"] = True

                    stats["total_models_with_data"] = len([m for m in stats["models_data"].values() if m["request_count"] > 0])
                    stats["total_requests"] = total_requests

                    conn.close()

                return {
                    "success": True,
                    "data": stats
                }

            except Exception as e:
                logger.error(f"[API_SERVER] 获取存储统计失败: {e}")
                return {"success": False, "error": str(e)}

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