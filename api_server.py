# api_server.py
from fastapi import FastAPI, Request, HTTPException, Response
from fastapi.responses import StreamingResponse, JSONResponse
import httpx
import logging
import json
import asyncio
from model_manager import ModelManager

logger = logging.getLogger(__name__)

app = FastAPI()
model_manager: ModelManager = None
async_clients = {}

async def get_async_client(port: int):
    if port not in async_clients:
        timeouts = httpx.Timeout(10.0, read=600.0)
        async_clients[port] = httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=timeouts)
    return async_clients[port]

async def stream_proxy_wrapper(model_alias: str, response: httpx.Response):
    """包装流式响应，以在结束后更新请求计数器。"""
    try:
        async for chunk in response.aiter_bytes():
            yield chunk
    finally:
        await response.aclose()
        model_manager.mark_request_completed(model_alias)

@app.get("/v1/models", response_class=JSONResponse)
async def list_models():
    """新增：获取模型列表的接口"""
    return model_manager.get_model_list()

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE", "OPTIONS", "HEAD"])
async def proxy_request(request: Request, path: str):
    if request.method == "OPTIONS":
        return Response(status_code=204, headers={
            "Access-Control-Allow-Origin": "*", "Access-Control-Allow-Methods": "*", "Access-Control-Allow-Headers": "*"})

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
        raise HTTPException(status_code=400, detail="请求体(JSON)中缺少 'model' 字段。")
    if model_alias not in model_manager.alias_to_primary_name:
        raise HTTPException(status_code=404, detail=f"模型别名 '{model_alias}' 未在配置中找到。")

    model_config = model_manager.get_model_config(model_alias)
    model_mode = model_config.get("mode", "Chat")

    is_chat_endpoint = "v1/chat/completions" in path
    is_completion_endpoint = "v1/completions" in path
    is_embedding_endpoint = "v1/embeddings" in path
    
    if model_mode == "Base" and is_chat_endpoint:
        raise HTTPException(status_code=400, detail=f"模型 '{model_alias}' 是 'Base' 模式, 不支持聊天补全接口。")
    if model_mode == "Chat" and is_completion_endpoint:
        raise HTTPException(status_code=400, detail=f"模型 '{model_alias}' 是 'Chat' 模式, 不支持文本补全接口。")
    if model_mode == "Embedding" and (is_chat_endpoint or is_completion_endpoint):
        raise HTTPException(status_code=400, detail=f"模型 '{model_alias}' 是 'Embedding' 模式, 不支持聊天或文本补全接口。")
    if model_mode in ["Chat", "Base"] and is_embedding_endpoint:
        raise HTTPException(status_code=400, detail=f"模型 '{model_alias}' 是 '{model_mode}' 模式, 不支持嵌入接口。")

    # --- 逻辑修改：将请求计数和所有后续操作包裹在try/except中 ---
    # 1. 在任何耗时操作前，立即增加待处理请求计数
    model_manager.increment_pending_requests(model_alias)
    try:
        # 2. 异步启动模型（如果未运行），失败则抛出异常
        success, message = await asyncio.to_thread(model_manager.start_model, model_alias)
        if not success:
            raise HTTPException(status_code=503, detail=message)
        
        # 3. 代理请求到下游模型
        target_port = model_config['port']
        client = await get_async_client(target_port)
        target_url = client.base_url.join(path)
        
        headers = dict(request.headers)
        headers.pop("host", None)
        headers.pop("content-length", None)
        headers.pop("transfer-encoding", None)

        req = client.build_request(
            request.method, target_url, headers=headers,
            content=request_data, params=request.query_params
        )
        response = await client.send(req, stream=True)
        
        is_streaming = "text/event-stream" in response.headers.get("content-type", "")

        if is_streaming:
            # 对于流式响应，计数器由 stream_proxy_wrapper 在结束后处理
            return StreamingResponse(
                stream_proxy_wrapper(model_alias, response),
                status_code=response.status_code,
                headers=dict(response.headers)
            )
        else:
            # 对于非流式响应，在此处直接标记完成
            content = await response.aread()
            await response.aclose()
            model_manager.mark_request_completed(model_alias)
            return Response(
                content=content,
                status_code=response.status_code,
                headers=dict(response.headers)
            )
            
    except httpx.ConnectError as e:
        logger.error(f"连接到模型 {model_alias} (端口:{target_port}) 失败: {e}")
        model_manager.mark_model_as_stopped(model_alias)
        # 异常被外层捕获，统一处理计数器
        raise HTTPException(status_code=502, detail=f"无法连接到下游模型 '{model_alias}'。") from e
    except Exception as e:
        # 统一的异常处理：只要请求失败，就将计数器减一
        logger.error(f"处理对 '{model_alias}' 的请求时出错: {e}", exc_info=True)
        model_manager.mark_request_completed(model_alias)
        if isinstance(e, HTTPException):
            raise  # 如果是已定义的HTTPException，直接重新抛出
        # 对于其他未知异常，包装成500错误
        raise HTTPException(status_code=500, detail=f"内部服务器错误: {str(e)}")

def run_api_server(manager: ModelManager, host: str, port: int):
    import uvicorn
    global model_manager
    model_manager = manager
    logger.info(f"统一API接口将在 http://{host}:{port} 上启动")
    uvicorn.run(app, host=host, port=port, log_level="warning")