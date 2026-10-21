"""反向代理:别名解析 → lifecycle ensure_running → httpx 转发 → SSE/非 SSE 分支 →
token 记录。无 facade Protocol —— 直接调用 lifecycle + state。

非流式路径 end_request 由 forward 的 finally 统一收口(streamed 标记区分流式路径,
后者由 _stream_wrapper finally 负责);_record_usage best-effort(写库失败不污染透传)。
响应头 _strip_headers(extra=connection/content-encoding) 去 hop-by-hop 头
(proxy 是 response 方向 hop-by-hop 参与者)。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections.abc import Mapping

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response, StreamingResponse

from llm_manager.gateway.aliases import resolve_alias_checked
from llm_manager.gateway.live_requests import LiveRequest, RequestRegistry, RequestStatus
from llm_manager.gateway.stream_capture import (
    StreamTextExtractor,
    extract_final_text,
    extract_preview,
    sanitize_body,
)

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


async def _record_usage(db, model, path, body_bytes, start, end) -> dict | None:
    """Best-effort:metering 写库失败不污染透传(非 stream 不改 status/body;stream 不截断)
    也不短路 end_request。吞所有异常 + log。同时累加进程内 session 统计(概览用)。

    返回 usage dict(抓取详情展示用),零 token / 解析失败返回 None。"""
    from llm_manager.data import metering
    from llm_manager.data import usage as _u

    try:
        usage = metering.parse_tokens(path, body_bytes)
        if not any(
            [usage.input_tokens, usage.output_tokens, usage.cache_tokens, usage.prompt_tokens]
        ):
            return None
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
        return {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "cache_tokens": usage.cache_tokens,
            "prompt_tokens": usage.prompt_tokens,
        }
    except Exception:
        logger.exception("record_usage failed for model=%s path=%s", model, path)
        return None


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


class _StreamGuard:
    """流式 pending 收尾编排(幂等),双通道:

    - 生成器 finally(「已启动」路径):同步 finish 最先(后续 aclose/record 的 await
      抛异常也不丢),然后撤销断连监听。
    - 断连监听(「从未启动」风险路径):客户端断连时 uvicorn 的 receive 恒返回
      http.disconnect(无论响应是否已开始发送)。监听任务在请求存活期间可靠拿到
      该消息并即时收尾。

    因此收尾锚定在「事件循环任务」而非「对象生命周期」:asyncio 的 asyncgen
    finalizer 会把从未关闭的生成器挂起到 loop 关闭(宿主进程运行期间不触发),
    基于 GC()__del__ 的兜底在常驻服务中不可靠,故不使用。

    抓取启用(live 非 None)时,守卫是断连路径的**权威终态写入者**:不能指望生成器
    finally 一定被调度到(取消作用域下第一个 await 就抛 CancelledError)。
    """

    def __init__(self, request: Request | None, model: str, live: LiveRequest | None = None):
        self._model = model
        self._live = live
        self._done = False
        self._task: asyncio.Task | None = None
        if request is not None:
            self._task = asyncio.get_running_loop().create_task(self._watch(request))

    async def _watch(self, request: Request) -> None:
        try:
            while True:
                msg = await request.receive()
                if msg is None:
                    return
                if msg.get("type") == "http.disconnect":
                    if self._live is not None:
                        self._live.mark_client_closed()
                    self.finish()
                    if self._live is not None:
                        self._live.finish(RequestStatus.CLIENT_CLOSED)
                        await self._live.abort_upstream()
                    return
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001, S110
            pass

    def finish(self) -> None:
        if self._done:
            return
        self._done = True
        from llm_manager import state

        state.end_request(self._model)

    def cancel_watch(self) -> None:
        if self._task is not None:
            self._task.cancel()


def _status_from(live: LiveRequest, error: str | None) -> RequestStatus:
    """终态优先级:cancel > client_closed > failed > completed。"""
    if live.cancel_requested:
        return RequestStatus.CANCELLED
    if live.client_closed:
        return RequestStatus.CLIENT_CLOSED
    if live.info.status is RequestStatus.CANCELLED:
        return RequestStatus.CANCELLED
    if error is not None:
        return RequestStatus.FAILED
    if live.info.http_status is not None and live.info.http_status >= 400:
        return RequestStatus.FAILED
    return RequestStatus.COMPLETED


async def _finalize(
    live: LiveRequest,
    guard: _StreamGuard | None,
    *,
    status: RequestStatus,
    db,
    path: str,
    model: str,
    payload: bytes,
    request_start: float,
    resp=None,
    error: str | None = None,
    finish_reason: str | None = None,
    has_reasoning: bool = False,
    has_tool_calls: bool = False,
) -> None:
    """统一收尾:终态**同步**落库在任何 await 之前,之后才补 usage/finish_reason。"""
    live.begin_finalize()
    if guard is not None:
        guard.finish()
    live.finish(status, error=error)  # 同步、幂等;必须早于下面任何 await
    try:
        usage = await _record_usage(db, model, path, payload, request_start, time.time())
        live.set_usage(usage)
        live.set_finish_reason(finish_reason)
        live.set_flags(has_reasoning=has_reasoning, has_tool_calls=has_tool_calls)
    except Exception:
        logger.debug("finalize enrichment failed for %s", live.info.id, exc_info=True)
    finally:
        if resp is not None:
            with contextlib.suppress(BaseException):
                await resp.aclose()


async def _stream_wrapper(
    resp,
    path,
    model,
    db,
    request_start,
    guard: _StreamGuard | None = None,
    live: LiveRequest | None = None,
    capture: StreamTextExtractor | None = None,
):
    from llm_manager import state

    sample = _StreamSample()
    error: str | None = None
    try:
        async for chunk in resp.aiter_bytes():
            sample.feed(chunk)
            if live is not None and capture is not None:
                try:
                    text = capture.feed(chunk)
                    if text:
                        live.append_text(text)
                except Exception:
                    logger.debug("capture failed model=%s path=%s", model, path, exc_info=True)
            yield chunk
    except Exception as e:
        error = str(e)
        raise
    finally:
        if live is None:
            # 抓取未启用:行为与改动前逐字节一致
            if guard is not None:
                guard.finish()
                guard.cancel_watch()
            else:
                state.end_request(model)
            await _record_usage(db, model, path, sample.sample(), request_start, time.time())
            await resp.aclose()
        else:
            tail_text = ""
            if capture is not None:
                try:
                    tail_text = capture.flush()
                except Exception:
                    logger.debug("capture flush failed for %s", live.info.id, exc_info=True)
            if tail_text:
                live.append_text(tail_text)
            await _finalize(
                live,
                guard,
                status=_status_from(live, error),
                db=db,
                path=path,
                model=model,
                payload=sample.sample(),
                request_start=request_start,
                resp=resp,
                error=error,
                finish_reason=capture.finish_reason if capture is not None else None,
                has_reasoning=capture.has_reasoning if capture is not None else False,
                has_tool_calls=capture.has_tool_calls if capture is not None else False,
            )


def _reject_absolute_url_path(path: str) -> None:
    """SSRF 守卫:catch-all {path:path} 剥前导 /,故请求 /http://evil.com/x 到达时
    path 恰为绝对 URL,httpx build_request 对绝对 URL 原样外发(绕开 base_url)。
    含 :// 的一律 400(相对子路径/空路径不含,照常转发)。"""
    if "://" in path:
        raise HTTPException(400, "absolute URLs not allowed in proxy path")


async def _send_with_cancel(client, built_request, live: LiveRequest | None):
    """可取消的上游发送:与 wait_cancelled 赛跑,覆盖"响应头还没回来"的 pre-header 阶段。"""
    if live is None:
        return await client.send(built_request, stream=True)
    send_task = asyncio.create_task(client.send(built_request, stream=True))
    cancel_task = asyncio.create_task(live.wait_cancelled())
    try:
        done, _pending = await asyncio.wait(
            {send_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED
        )
    finally:
        cancel_task.cancel()
        with contextlib.suppress(BaseException):
            await cancel_task
    if send_task in done:
        return send_task.result()
    send_task.cancel()
    with contextlib.suppress(BaseException):
        await send_task
    raise HTTPException(499, "request cancelled")


async def forward(
    request: Request,
    path: str,
    lifecycle,
    cfg,
    db,
    client_pool,
    registry: RequestRegistry | None = None,
) -> Response:
    _reject_absolute_url_path(path)
    from llm_manager import state
    from llm_manager.state import ModelStatus

    t0 = time.monotonic()
    body = await _read_body(request)
    alias = _extract_model_alias(body)
    primary = resolve_alias_checked(cfg, alias)
    logger.info("REQ %s /%s model=%s", request.method, path, primary)
    served = cfg.models[primary].aliases[0]  # aliases[0]=主别名=下游 served name

    status = await lifecycle.ensure_running(primary, inc_pending=True)
    if status != ModelStatus.ROUTING:
        logger.warning("model %s not routing (%s)", primary, status.value)
        raise HTTPException(503, f"model '{primary}' not routing (status={status.value})")

    # 抓取记录在"真正要发上游"之后建(等待启动/启动失败的请求不进列表),但脱敏副本
    # 必须在改写 body 之前取 —— 用户要看的是自己发了什么,不是代理改写后的样子。
    live: LiveRequest | None = None
    if registry is not None:
        try:
            is_dict = isinstance(body, dict)
            raw = b"" if is_dict else (body if isinstance(body, bytes) else b"")
            live = registry.start(
                model=primary,
                alias=served,
                method=request.method,
                path="/" + path.lstrip("/"),
                stream=_is_stream(body),
                request_preview=(
                    extract_preview(body) if is_dict else raw[:300].decode("utf-8", "replace")
                ),
                request_body=sanitize_body(body) if is_dict else None,
                input_text=None if is_dict else raw[:4096].decode("utf-8", "replace"),
            )
        except Exception:
            # 抓取/脱敏失败绝不能变成 500:放弃这条记录,请求照常转发。
            logger.debug("request capture failed to start", exc_info=True)
            live = None

    if isinstance(body, dict):
        body["model"] = served  # 内部统一用 aliases[0] 调下游
        if _is_stream(body):
            body = _inject_include_usage(body, path)
        request_data = _reserialize(body)
    else:
        request_data = body if isinstance(body, bytes) else b""

    request_start = time.time()
    resp = None  # 池化 httpx 连接;错误路径必须 aclose 归还(见下两个 except)
    streamed = False  # 流式路径 end_request 由 _stream_wrapper finally 负责,外层跳过
    try:
        port = cfg.models[primary].port
        client = _get_or_create_client(client_pool, port)
        resp = await _send_with_cancel(
            client,
            client.build_request(
                request.method,
                path,
                headers=_strip_headers(request.headers, extra=("host",)),
                content=request_data,
                params=request.query_params,
            ),
            live,
        )
        if live is not None:
            live.set_http_status(resp.status_code)
        if _detect_sse(resp):
            logger.info(
                "RESP %d stream model=%s %.2fs", resp.status_code, primary, time.monotonic() - t0
            )
            streamed = True
            capture = None
            if live is not None:
                live.attach_upstream(resp)  # 取消/断连要能关掉这条连接
                capture = StreamTextExtractor(path)
            return StreamingResponse(
                _stream_wrapper(
                    resp,
                    path,
                    primary,
                    db,
                    request_start,
                    guard=_StreamGuard(request, primary, live),
                    live=live,
                    capture=capture,
                ),
                status_code=resp.status_code,
                headers=_strip_headers(resp.headers, extra=("connection", "content-encoding")),
            )

        # 非流式:可取消读取循环(取消只在不产生半截响应时才有意义)
        buf = bytearray()
        cancelled = False
        try:
            async for chunk in resp.aiter_bytes():
                if live is not None and live.cancel_requested:
                    cancelled = True
                    break
                buf.extend(chunk)
        finally:
            # 读中断(上游错误/取消)也要 aclose,否则池化连接泄漏不复用
            await resp.aclose()
        status_code = resp.status_code
        headers = _strip_headers(resp.headers, extra=("connection", "content-encoding"))
        resp = None  # 已关闭,防 except 重复 aclose
        if cancelled and live is not None:
            live.finish(RequestStatus.CANCELLED)
            raise HTTPException(499, "request cancelled")
        content = bytes(buf)
        if live is not None:
            finish_reason = None
            has_reasoning = False
            has_tool_calls = False
            try:
                result = extract_final_text(path, content)
                live.append_text(result.text)
                finish_reason = result.finish_reason
                has_reasoning = result.has_reasoning
                has_tool_calls = result.has_tool_calls
            except Exception:
                logger.debug("capture failed for non-stream %s", live.info.id, exc_info=True)
            await _finalize(
                live,
                None,
                status=(RequestStatus.FAILED if status_code >= 400 else RequestStatus.COMPLETED),
                db=db,
                path=path,
                model=primary,
                payload=content,
                request_start=request_start,
                finish_reason=finish_reason,
                has_reasoning=has_reasoning,
                has_tool_calls=has_tool_calls,
            )
        else:
            await _record_usage(db, primary, path, content, request_start, time.time())
        logger.info("RESP %d model=%s %.2fs", status_code, primary, time.monotonic() - t0)
        return Response(content=content, status_code=status_code, headers=headers)
    except HTTPException:
        if live is not None and live.cancel_requested:
            live.finish(RequestStatus.CANCELLED)
        raise
    except httpx.HTTPError as e:
        if resp is not None:
            await resp.aclose()
        if live is not None:
            live.finish(RequestStatus.FAILED, error=str(e))
        logger.warning("upstream error model=%s: %s", primary, e)
        raise HTTPException(502, f"upstream error: {e}")
    except Exception as e:  # noqa: BLE001
        if resp is not None:
            await resp.aclose()
        if live is not None:
            live.finish(RequestStatus.FAILED, error=str(e))
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
    registry: RequestRegistry | None = None,
) -> None:
    """挂载 OpenAI 兼容代理的 catch-all(POST/PUT/DELETE/PATCH)。一方法一 handler,
    各自独立 operationId(单 api_route 4 方法会撞同 operationId,产生重复键破坏
    OpenAPI 消费者如前端 codegen)。读穿:_forward 每请求从 ConfigStore 取 fresh cfg。"""

    async def _forward(path: str, request: Request) -> Response:
        cfg = request.app.state.config_store.snapshot()  # 读穿:每请求 fresh(CRUD 后新别名可路由)
        return await forward(request, path, lifecycle, cfg, db, client_pool, registry=registry)

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
