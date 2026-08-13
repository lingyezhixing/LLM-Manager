"""Reverse proxy: alias resolve → lifecycle ensure_running → httpx forward →
SSE/non-SSE branch → token record. No facade Protocol — calls lifecycle + state.

非流式路径 end_request 由 forward 的 finally 统一收口(streamed 标记区分流式路径,
后者由 _stream_wrapper finally 负责);_record_usage best-effort(写库失败不污染透传)。
响应头 _strip_headers(extra=connection/content-encoding) 去 hop-by-hop 头
(proxy 是 response 方向 hop-by-hop 参与者)。"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Mapping

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from llm_manager.gateway.aliases import resolve_alias_checked

logger = logging.getLogger(__name__)

_STRIP_BASE = {"content-length", "transfer-encoding"}


def _strip_headers(headers: Mapping[str, str], extra: tuple[str, ...] = ()) -> dict[str, str]:
    """剥离基集(hop-by-hop 通用)+ 每侧额外键:request 侧 +host,response 侧
    +connection/content-encoding。剥离集合与原两个函数逐项一致。"""
    bad = _STRIP_BASE | set(extra)
    return {k: v for k, v in headers.items() if k.lower() not in bad}


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
        except Exception:  # noqa: BLE001
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
    from llm_manager.data import usage as _u

    try:
        usage = metering.parse_tokens(path, body_bytes)
        if not any(
            [usage.input_tokens, usage.output_tokens, usage.cache_tokens, usage.prompt_tokens]
        ):
            return
        _u.session_add(
            usage.input_tokens, usage.output_tokens, usage.cache_tokens, usage.prompt_tokens
        )
        await asyncio.to_thread(
            _u.record_usage,
            db,
            model,
            start,
            end,
            usage.input_tokens,
            usage.output_tokens,
            usage.cache_tokens,
            usage.prompt_tokens,
        )
    except Exception:
        logger.exception("record_usage failed for model=%s path=%s", model, path)


class _StreamSample:
    """头尾双缓冲:只保留首 HEAD_MAX 字节 + 末 TAIL_MAX 字节供 metering 解析用量,
    避免长流式响应(推理模型几分钟输出)整条缓冲导致内存随流时长无界增长、N 个并发流
    = N 份无界缓冲。

    metering 各解析器的用量字段仅出现在头部(Anthropic message_start 的 input 用量)
    或尾部(OpenAI usage / Anthropic message_delta / Responses response.completed /
    Ollama done 末块 / Gemini 累积 usageMetadata / Cohere 末块 meta)——中间内容增量
    不含用量,可安全丢弃。解析器对头尾拼接串容忍:拼接处的半行被
    _try_json 回退跳过,完整的 head/tail 事件照常解析(parse_anthropic 需头+尾,
    其余仅需尾)。"""

    def __init__(self, head_max: int = 16 * 1024, tail_max: int = 128 * 1024) -> None:
        self._head_max = head_max
        self._tail_max = tail_max
        self._head = bytearray()
        self._tail = bytearray()

    def feed(self, chunk: bytes) -> None:
        if len(self._head) < self._head_max:
            self._head.extend(chunk[: self._head_max - len(self._head)])
        self._tail.extend(chunk)
        if len(self._tail) > self._tail_max:
            del self._tail[: len(self._tail) - self._tail_max]

    def sample(self) -> bytes:
        # 全流 ≤ head 时 head 已含全部,直接返回(避免与 tail 重复拼接致事件重复)。
        if len(self._head) < self._head_max:
            return bytes(self._head)
        return bytes(self._head) + bytes(self._tail)


async def _stream_wrapper(resp, path, model, db, request_start):
    from llm_manager import state

    sample = _StreamSample()
    try:
        async for chunk in resp.aiter_bytes():
            sample.feed(chunk)
            yield chunk
    finally:
        await resp.aclose()
        await _record_usage(db, model, path, sample.sample(), request_start, time.time())
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
    resp = None  # 池化 httpx 连接;错误路径必须 aclose 归还(见下两个 except)
    streamed = False  # 流式路径 end_request 由 _stream_wrapper finally 负责,外层跳过
    try:
        port = cfg.models[primary].port
        client = _get_or_create_client(client_pool, port)
        resp = await client.send(
            client.build_request(
                request.method,
                path,
                headers=_strip_headers(request.headers, extra=("host",)),
                content=request_data,
                params=request.query_params,
            ),
            stream=True,
        )
        if _detect_sse(resp):
            logger.info(
                "RESP %d stream model=%s %.2fs", resp.status_code, primary, time.monotonic() - t0
            )
            streamed = True
            return StreamingResponse(
                _stream_wrapper(resp, path, primary, db, request_start),
                status_code=resp.status_code,
                headers=_strip_headers(resp.headers, extra=("connection", "content-encoding")),
            )
        try:
            content = await resp.aread()
        finally:
            # aread 抛错(上游中断)也要 aclose,否则池化连接泄漏不复用
            await resp.aclose()
        status_code = resp.status_code
        headers = _strip_headers(resp.headers, extra=("connection", "content-encoding"))
        resp = None  # 已关闭,防 except 重复 aclose
        await _record_usage(db, primary, path, content, request_start, time.time())
        logger.info("RESP %d model=%s %.2fs", status_code, primary, time.monotonic() - t0)
        return Response(content=content, status_code=status_code, headers=headers)
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        if resp is not None:
            await resp.aclose()
        logger.warning("upstream error model=%s: %s", primary, e)
        raise HTTPException(502, f"upstream error: {e}")
    except Exception as e:  # noqa: BLE001
        if resp is not None:
            await resp.aclose()
        logger.warning("internal model=%s: %s", primary, e)
        raise HTTPException(500, f"internal: {e}")
    finally:
        if not streamed:
            state.end_request(primary)


def register_proxy_routes(
    app: FastAPI,
    lifecycle,
    db,
    client_pool,
) -> None:
    """挂载 OpenAI 兼容代理的 catch-all(POST/PUT/DELETE/PATCH)。一方法一 handler,
    各自独立 operationId(单 api_route 4 方法会撞同 operationId,产生重复键破坏
    OpenAPI 消费者如前端 codegen)。读穿:_forward 每请求从 ConfigStore 取 fresh cfg。"""

    async def _forward(path: str, request: Request) -> Response:
        cfg = request.app.state.config_store.snapshot()  # 读穿:每请求 fresh(CRUD 后新别名可路由)
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
