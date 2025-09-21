#!/usr/bin/env python3
"""
现代化WebUI服务器
基于FastAPI的现代化Web界面
"""

import asyncio
import logging
import os
import time
from typing import Dict, List, Any, Optional
import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
import json
from utils.logger import get_logger
from core.model_controller import ModelController

logger = get_logger(__name__)

class WebUIServer:
    """现代化WebUI服务器"""

    def __init__(self, model_controller: ModelController):
        self.model_controller = model_controller
        self.app = FastAPI(title="LLM-Manager WebUI", version="2.0.0")
        self.active_connections: List[WebSocket] = []
        self.token_consumption: Dict[str, Dict[str, Any]] = {}
        self.throughput_history: List[Dict[str, Any]] = []
        self.max_history_points = 100

        # 价格配置（默认值）
        self.input_token_price = 0.001  # $ per 1K tokens
        self.output_token_price = 0.002  # $ per 1K tokens

        self.setup_routes()
        self.setup_middleware()

    def setup_middleware(self):
        """设置中间件"""
        self.app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    def setup_routes(self):
        """设置路由"""

        @self.app.get("/", response_class=HTMLResponse)
        async def get_index():
            """返回主页"""
            index_path = os.path.join(os.path.dirname(__file__), "index.html")
            if os.path.exists(index_path):
                return FileResponse(index_path)
            return HTMLResponse(content=self.generate_fallback_html())

        @self.app.get("/api/models")
        async def get_models():
            """获取模型列表"""
            try:
                models_status = self.model_controller.get_all_models_status()
                models_list = []

                for primary_name, status in models_status.items():
                    model_info = {
                        "id": primary_name,
                        "object": "model",
                        "created": int(time.time()),
                        "owned_by": "user",
                        "status": status["status"],
                        "mode": status["mode"],
                        "port": self._get_model_port(primary_name),
                        "pending_requests": status["pending_requests"],
                        "throughput": self._get_model_throughput(primary_name),
                        "aliases": status["aliases"],
                        "failure_reason": status.get("failure_reason")
                    }
                    models_list.append(model_info)

                return {"object": "list", "data": models_list}
            except Exception as e:
                logger.error(f"获取模型列表失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/models/{model_id}/start")
        async def start_model(model_id: str):
            """启动模型"""
            try:
                success, message = self.model_controller.start_model(model_id)
                if success:
                    await self.broadcast_update({"type": "model_started", "model_id": model_id})
                    return {"success": True, "message": message}
                else:
                    return {"success": False, "message": message}
            except Exception as e:
                logger.error(f"启动模型失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.post("/api/models/{model_id}/stop")
        async def stop_model(model_id: str):
            """停止模型"""
            try:
                success, message = self.model_controller.stop_model(model_id)
                if success:
                    await self.broadcast_update({"type": "model_stopped", "model_id": model_id})
                    return {"success": True, "message": message}
                else:
                    return {"success": False, "message": message}
            except Exception as e:
                logger.error(f"停止模型失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/models/{model_id}/logs")
        async def get_model_logs(model_id: str):
            """获取模型日志"""
            try:
                logs = self.model_controller.get_model_logs(model_id)
                return {"logs": logs}
            except Exception as e:
                logger.error(f"获取模型日志失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/system/status")
        async def get_system_status():
            """获取系统状态"""
            try:
                gpu_status = self._get_gpu_status()
                models_status = self.model_controller.get_all_models_status()

                # 计算系统统计
                total_models = len(models_status)
                running_models = sum(1 for m in models_status.values() if m["status"] == "routing")
                total_throughput = sum(self._get_model_throughput(name) for name in models_status.keys())

                return {
                    "total_models": total_models,
                    "running_models": running_models,
                    "total_throughput": total_throughput,
                    "gpu_status": gpu_status,
                    "timestamp": time.time()
                }
            except Exception as e:
                logger.error(f"获取系统状态失败: {e}")
                raise HTTPException(status_code=500, detail=str(e))

        @self.app.get("/api/analytics/throughput")
        async def get_throughput_data():
            """获取吞吐量数据"""
            return {"data": self.throughput_history}

        @self.app.get("/api/analytics/tokens")
        async def get_token_data():
            """获取Token消耗数据"""
            return {"data": self.token_consumption}

        @self.app.get("/api/analytics/cost")
        async def get_cost_data():
            """获取成本数据"""
            total_cost = self._calculate_total_cost()
            return {
                "total_cost": total_cost,
                "input_price": self.input_token_price,
                "output_price": self.output_token_price,
                "daily_cost": total_cost,
                "monthly_cost": total_cost * 30
            }

        @self.app.post("/api/analytics/prices")
        async def update_prices(input_price: float, output_price: float):
            """更新价格设置"""
            self.input_token_price = input_price
            self.output_token_price = output_price
            return {"success": True}

        @self.app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket连接"""
            await websocket.accept()
            self.active_connections.append(websocket)

            try:
                while True:
                    data = await websocket.receive_text()
                    # 处理客户端消息
                    try:
                        message = json.loads(data)
                        await self.handle_websocket_message(message, websocket)
                    except json.JSONDecodeError:
                        await websocket.send_json({"error": "Invalid JSON"})
            except WebSocketDisconnect:
                self.active_connections.remove(websocket)

    def _get_model_port(self, model_id: str) -> Optional[int]:
        """获取模型端口"""
        config = self.model_controller.get_model_config(model_id)
        return config.get("port") if config else None

    def _get_model_throughput(self, model_id: str) -> float:
        """获取模型吞吐量（模拟数据）"""
        # 这里应该根据实际API调用来计算
        # 现在返回模拟数据
        status = self.model_controller.models_state.get(model_id, {})
        if status.get("status") == "routing":
            return 50.0 + (hash(model_id) % 100)  # 模拟吞吐量
        return 0.0

    def _get_gpu_status(self) -> List[Dict[str, Any]]:
        """获取GPU状态"""
        gpu_status = []
        try:
            for device_name, device_plugin in self.model_controller.device_plugins.items():
                if device_plugin.is_online():
                    total_mb, available_mb, used_mb = device_plugin.get_memory_info()
                    gpu_status.append({
                        "device_name": device_name,
                        "total_memory_mb": total_mb,
                        "available_memory_mb": available_mb,
                        "used_memory_mb": used_mb,
                        "utilization_percent": (used_mb / total_mb * 100) if total_mb > 0 else 0
                    })
        except Exception as e:
            logger.error(f"获取GPU状态失败: {e}")

        return gpu_status

    def _calculate_total_cost(self) -> float:
        """计算总成本"""
        total_cost = 0.0
        for model_data in self.token_consumption.values():
            input_tokens = model_data.get("input_tokens", 0)
            output_tokens = model_data.get("output_tokens", 0)
            total_cost += (input_tokens * self.input_token_price / 1000) + \
                         (output_tokens * self.output_token_price / 1000)
        return total_cost

    async def handle_websocket_message(self, message: Dict[str, Any], websocket: WebSocket):
        """处理WebSocket消息"""
        msg_type = message.get("type")

        if msg_type == "subscribe_updates":
            # 订阅更新，开始推送实时数据
            await self.start_real_time_updates(websocket)
        elif msg_type == "get_console":
            # 获取控制台输出
            model_id = message.get("model_id")
            if model_id:
                logs = self.model_controller.get_model_logs(model_id)
                await websocket.send_json({
                    "type": "console_update",
                    "model_id": model_id,
                    "logs": logs
                })

    async def start_real_time_updates(self, websocket: WebSocket):
        """开始实时更新"""
        try:
            while True:
                # 发送系统状态更新
                status_data = {
                    "type": "system_status",
                    "data": await self.get_system_status()
                }
                await websocket.send_json(status_data)

                # 更新吞吐量历史
                self._update_throughput_history()

                await asyncio.sleep(5)  # 每5秒更新一次

        except Exception as e:
            logger.error(f"实时更新失败: {e}")

    def _update_throughput_history(self):
        """更新吞吐量历史"""
        try:
            models_status = self.model_controller.get_all_models_status()
            total_throughput = sum(self._get_model_throughput(name) for name in models_status.keys())

            current_time = time.time()
            self.throughput_history.append({
                "timestamp": current_time,
                "throughput": total_throughput
            })

            # 保持历史数据在合理范围内
            if len(self.throughput_history) > self.max_history_points:
                self.throughput_history.pop(0)

        except Exception as e:
            logger.error(f"更新吞吐量历史失败: {e}")

    async def broadcast_update(self, message: Dict[str, Any]):
        """广播更新给所有连接的客户端"""
        if not self.active_connections:
            return

        for connection in self.active_connections:
            try:
                await connection.send_json(message)
            except:
                # 连接可能已经断开，移除它
                if connection in self.active_connections:
                    self.active_connections.remove(connection)

    def generate_fallback_html(self) -> str:
        """生成备用HTML内容"""
        return """
        <!DOCTYPE html>
        <html lang="zh-CN">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>LLM-Manager 控制台</title>
            <style>
                body { font-family: Arial, sans-serif; background: #1a1a1a; color: white; margin: 0; padding: 20px; }
                .container { max-width: 1200px; margin: 0 auto; }
                .header { text-align: center; margin-bottom: 30px; }
                .models { display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 20px; }
                .model-card { background: #2a2a2a; padding: 20px; border-radius: 10px; }
                .status { display: inline-block; padding: 5px 10px; border-radius: 5px; font-size: 12px; }
                .status.running { background: #10b981; }
                .status.stopped { background: #ef4444; }
                .status.starting { background: #f59e0b; }
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <h1>🧠 LLM-Manager 控制台</h1>
                    <p>现代化Web界面</p>
                </div>
                <div id="models-container" class="models">
                    <div>正在加载模型列表...</div>
                </div>
            </div>
            <script>
                async function loadModels() {
                    try {
                        const response = await fetch('/api/models');
                        const data = await response.json();
                        const container = document.getElementById('models-container');

                        if (data.data && data.data.length > 0) {
                            container.innerHTML = data.data.map(model => `
                                <div class="model-card">
                                    <h3>${model.id}</h3>
                                    <p>模式: ${model.mode || 'Chat'}</p>
                                    <p>状态: <span class="status ${model.status}">${model.status}</span></p>
                                    <p>端口: ${model.port || 'N/A'}</p>
                                    <p>待处理请求: ${model.pending_requests || 0}</p>
                                </div>
                            `).join('');
                        } else {
                            container.innerHTML = '<div>未找到模型配置</div>';
                        }
                    } catch (error) {
                        container.innerHTML = '<div>加载模型列表失败</div>';
                    }
                }

                loadModels();
                setInterval(loadModels, 5000);
            </script>
        </body>
        </html>
        """

def run_webui_server(model_controller: ModelController, host: str = "127.0.0.1", port: int = 10000):
    """运行WebUI服务器"""
    import socket

    # 检查端口是否被占用
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind((host, port))
    except OSError:
        logger.warning(f"端口 {port} 已被占用，尝试使用端口 {port + 1}")
        port = port + 1

    server = WebUIServer(model_controller)

    logger.info(f"启动现代化WebUI服务器: http://{host}:{port}")

    # 直接启动服务器，不使用Config对象
    uvicorn.run(server.app, host=host, port=port, log_level="info", access_log=False)