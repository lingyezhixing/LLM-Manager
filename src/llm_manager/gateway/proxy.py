"""Reverse proxy: alias resolve → lifecycle ensure_running → httpx forward →
SSE/non-SSE branch → token record. No facade Protocol — calls lifecycle + state.

end_request 分布三处(非 stream return 前 / 各 except / _stream_wrapper finally)
防 pending 泄漏。_record_usage best-effort(写库失败不污染透传、不短路 end_request)。
响应头 _strip_response_headers 去 content-length/transfer-encoding/connection/
content-encoding(proxy 是 response 方向 hop-by-hop 参与者)。"""
from __future__ import annotations

import asyncio
import json
import logging
import time

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from llm_manager.gateway.aliases import resolve_alias_checked

logger = logging.getLogger(__name__)

_RESP_HOP_BY_HOP = {"content-length", "transfer-encoding", "connection", "content-encoding"}


def _strip_headers(headers) -> dict:
    bad = {"host", "content-length", "transfer-encoding"}
    return {k: v for k, v in headers.items() if k.lower() not in bad}


def _strip_response_headers(headers) -> dict:
    return {k: v for k, v in headers.items() if k.lower() not in _RESP_HOP_BY_HOP}


def _detect_sse(resp) -> bool:
    return "text/event-stream" in resp.headers.get("content-type", "")


def _extract_model_alias(body) -> str | None:
    return body.get("model") if isinstance(body, dict) else None


def _is_stream(body) -> bool:
    return isinstance(body, dict) and body.get("stream") is True


def _reserialize(body: dict) -> bytes:
    return json.dumps(body).encode("utf-8")


async def _read_body(request: Request):
    if "application/json" in request.headers.get("content-type", ""):
        try:
            return await request.json()
        except Exception:
            return await request.body()
    return await request.body()


def _get_or_create_client(pool: dict, port: int) -> httpx.AsyncClient:
    client = pool.get(port)
    if client is None:
        client = httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}",
            timeout=httpx.Timeout(30.0, read=600.0, connect=30.0, write=30.0),
        )
        pool[port] = client
    return client


def _inject_include_usage(body: dict, path: str) -> dict:
    from llm_manager.data import metering
    if metering.needs_include_usage(path):
        so = dict(body.get("stream_options") or {})
        so.setdefault("include_usage", True)
        body["stream_options"] = so
    return body


async def _record_usage(db, model, path, body_bytes, start, end) -> None:
    """Best-effort:metering 写库失败不污染透传(非 stream 不改 status/body;stream 不截断)
    也不短路 end_request。吞所有异常 + log。同时累加进程内 session 统计(概览用)。"""
    from llm_manager.data import metering
    from llm_manager.data import persistence as _p
    from llm_manager.data import session as _s
    try:
        usage = metering.parse_tokens(path, body_bytes)
        if not any([usage.input_tokens, usage.output_tokens, usage.cache_tokens, usage.prompt_tokens]):
            return
        _s.add(usage.input_tokens, usage.output_tokens, usage.cache_tokens, usage.prompt_tokens)
        await asyncio.to_thread(
            _p.record_usage, db, model, start, end,
            usage.input_tokens, usage.output_tokens, usage.cache_tokens, usage.prompt_tokens,
        )
    except Exception:
        logger.exception("record_usage failed for model=%s path=%s", model, path)


async def _stream_wrapper(resp, path, model, db, request_start):
    from llm_manager import state
    chunks: list[bytes] = []
    try:
        async for chunk in resp.aiter_bytes():
            chunks.append(chunk)
            yield chunk
    finally:
        await resp.aclose()
        await _record_usage(db, model, path, b"".join(chunks), request_start, time.time())
        state.end_request(model)


async def forward(request: Request, path: str, lifecycle, cfg, db, client_pool) -> Response:
    from llm_manager import state
    from llm_manager.state import ModelStatus

    t0 = time.monotonic()
    body = await _read_body(request)
    alias = _extract_model_alias(body)
    primary = resolve_alias_checked(cfg, alias)
    logger.info("REQ %s /%s model=%s", request.method, path, primary)
    served = cfg.models[primary].aliases[0]  # aliases[0]=主别名=下游 served name
    if isinstance(body, dict):
        body["model"] = served  # 内部统一用 aliases[0] 调下游
        if _is_stream(body):
            body = _inject_include_usage(body, path)
        request_data = _reserialize(body)
    else:
        request_data = body if isinstance(body, bytes) else b""

    status = await lifecycle.ensure_running(primary, inc_pending=True)
    if status != ModelStatus.ROUTING:
        logger.warning("model %s not routing (%s)", primary, status.value)
        raise HTTPException(503, f"model '{primary}' not routing (status={status.value})")

    request_start = time.time()
    try:
        port = cfg.models[primary].port
        client = _get_or_create_client(client_pool, port)
        resp = await client.send(
            client.build_request(request.method, path,
                headers=_strip_headers(request.headers), content=request_data,
                params=request.query_params),
            stream=True)
        if _detect_sse(resp):
            logger.info("RESP %d stream model=%s %.2fs", resp.status_code, primary, time.monotonic() - t0)
            return StreamingResponse(
                _stream_wrapper(resp, path, primary, db, request_start),
                status_code=resp.status_code, headers=_strip_response_headers(resp.headers))
        content = await resp.aread()
        await resp.aclose()
        await _record_usage(db, primary, path, content, request_start, time.time())
        state.end_request(primary)
        logger.info("RESP %d model=%s %.2fs", resp.status_code, primary, time.monotonic() - t0)
        return Response(content=content, status_code=resp.status_code,
                        headers=_strip_response_headers(resp.headers))
    except HTTPException:
        state.end_request(primary)
        raise
    except httpx.HTTPError as e:
        state.end_request(primary)
        logger.warning("upstream error model=%s: %s", primary, e)
        raise HTTPException(502, f"upstream error: {e}")
    except Exception as e:
        state.end_request(primary)
        logger.warning("internal model=%s: %s", primary, e)
        raise HTTPException(500, f"internal: {e}")


def register_proxy_routes(
    app: FastAPI, lifecycle, db, client_pool,
) -> None:
    """挂载 OpenAI 兼容代理的 catch-all(POST/PUT/DELETE/PATCH)。一方法一 handler,
    各自独立 operationId(单 api_route 4 方法会撞同 operationId,产生重复键破坏
    OpenAPI 消费者如前端 codegen)。读穿:_forward 每请求从 ConfigStore 取 fresh cfg。"""
    async def _forward(path: str, request: Request) -> Response:
        cfg = request.app.state.config_store.snapshot()   # 读穿:每请求 fresh(P2 CRUD 后新别名可路由)
        return await forward(request, path, lifecycle, cfg, db, client_pool)

    @app.post("/{path:path}", operation_id="catch_all__path__post")
    async def catch_all_post(path: str, request: Request) -> Response:
        return await _forward(path, request)

    @app.put("/{path:path}", operation_id="catch_all__path__put")
    async def catch_all_put(path: str, request: Request) -> Response:
        return await _forward(path, request)

    @app.delete("/{path:path}", operation_id="catch_all__path__delete")
    async def catch_all_delete(path: str, request: Request) -> Response:
        return await _forward(path, request)

    @app.patch("/{path:path}", operation_id="catch_all__path__patch")
    async def catch_all_patch(path: str, request: Request) -> Response:
        return await _forward(path, request)
