"""反向代理:别名解析 → lifecycle ensure_running → httpx 转发 → SSE/非 SSE 分支 →
token 记录。无 facade Protocol —— 直接调用 lifecycle + state。

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

from llm_manager.config import parse_cloud_id
from llm_manager.gateway.aliases import resolve_alias_checked
from llm_manager.gateway.cloud import (
    apply_extra_headers,
    build_auth_headers,
    classify_path,
    family_default_auth_style,
    join_url,
    mapping_for,
    resolve_cloud_model,
    resolve_global_mapping,
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


async def _record_usage(db, model, path, body_bytes, start, end, source: str = "local") -> None:
    """Best-effort:metering 写库失败不污染透传(非 stream 不改 status/body;stream 不截断)
    也不短路 end_request。吞所有异常 + log。同时累加进程内 session 统计(概览用)。
    source 透传 record_usage('local'/'cloud'),云端流调用方传 'cloud'。"""
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
            source,
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


class _StreamGuard:
    """流式 pending 收尾编排(幂等),双通道:

    - 生成器 finally(「已启动」路径):同步 finish 最先(后续 aclose/record 的 await
      抛异常也不丢),然后撤销断连监听。
    - 断连监听(「从未启动」风险路径):客户端断连时 uvicorn 的 receive 恒返回
      http.disconnect(无论响应是否已开始发送)。监听任务在请求存活期间可靠拿到
      该消息并即时收尾。

    因此收尾锚定在「事件循环任务」而非「对象生命周期」:asyncio 的 asyncgen
    finalizer 会把从未关闭的生成器挂起到 loop 关闭(宿主进程运行期间不触发),
    基于 GC()__del__ 的兜底在常驻服务中不可靠,故不使用。"""

    def __init__(self, request: Request | None, model: str) -> None:
        self._model = model
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
                    self.finish()
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


async def _stream_wrapper(resp, path, model, db, request_start, guard: _StreamGuard | None = None):
    from llm_manager import state

    sample = _StreamSample()
    try:
        async for chunk in resp.aiter_bytes():
            sample.feed(chunk)
            yield chunk
    finally:
        if guard is not None:
            guard.finish()
            guard.cancel_watch()
        else:
            state.end_request(model)
        await _record_usage(db, model, path, sample.sample(), request_start, time.time())
        await resp.aclose()


async def _cloud_stream_wrapper(resp, path, db, anchor, request_start):
    """云端流式:仅头尾采样 + _record_usage(source='cloud') + aclose。state-free。"""
    sample = _StreamSample()
    try:
        async for chunk in resp.aiter_bytes():
            sample.feed(chunk)
            yield chunk
    finally:
        await _record_usage(
            db, anchor, path, sample.sample(), request_start, time.time(), source="cloud"
        )
        await resp.aclose()


async def forward_cloud(
    request,
    path,
    cfg,
    db,
    cloud_client,
    provider_name,
    provider,
    cm,
    body,
) -> Response:
    """云端流(标准/自定义映射共用):state-free——不 ensure_running/begin/end_request。

    cm: CloudModel | None。None=model 缺失路径(全局映射回退)→ 归因 {provider}。
    """
    from llm_manager import state  # noqa: F401 — 仅标记:本函数严禁触碰 state

    if not provider.enabled:
        raise HTTPException(503, f"provider '{provider_name}' is disabled")
    if cloud_client is None:
        raise HTTPException(500, "cloud client not initialized")

    mapping = mapping_for(provider, path)
    if mapping is not None:
        url = mapping.target_url
        family = None
        auth_style = mapping.auth_style
    else:
        family = classify_path(path)
        if family is None:
            raise HTTPException(
                404, f"provider '{provider_name}' has no endpoint for path '/{path}'"
            )
        base = getattr(provider, f"{family}_base")
        if not base:
            raise HTTPException(404, f"provider '{provider_name}' 未配置该接口")
        url = join_url(base, path, family)
        auth_style = family_default_auth_style(family)

    model_anchor = provider_name
    if isinstance(body, dict):
        if cm is not None:
            body["model"] = cm.model_name
            model_anchor = f"{provider_name}/{cm.model_name}"
        else:
            bmodel = body.get("model")
            if isinstance(bmodel, str):
                parsed = parse_cloud_id(bmodel)
                if parsed is not None and parsed[0] == provider_name:
                    for m in provider.models:
                        if m.model_name == parsed[1]:
                            body["model"] = m.model_name
                            model_anchor = bmodel
                            break
        if _is_stream(body):
            body = _inject_include_usage(body, path)
        request_data = _reserialize(body)
    else:
        request_data = body if isinstance(body, bytes) else b""

    request_start = time.time()
    # 客户端鉴权头并入剥离集合(而非按小写键 pop):starlette Headers 保留 wire
    # 原始大小写,pop("authorization") 对 "Authorization"/"X-API-Key" 变体不命中,
    # 会与注入的服务商凭证双发出站(上游取头顺序不定 → 401 或密钥泄漏)。
    headers = _strip_headers(request.headers, extra=("host", "authorization", "x-api-key"))
    headers.update(build_auth_headers(provider, family, auth_style))
    apply_extra_headers(headers, provider)

    logger.info("CLOUD REQ %s %s provider=%s", request.method, url, provider_name)
    # target_url 自带 query 与客户端 query 同名冲突时目标参数优先
    # (httpx 默认以客户端 params 覆盖 URL 自带 query);客户端未冲突参数仍透传。
    # 做法:URL 拆出自带 query 剥离重发,统一经 params 合并(客户端打底、目标覆盖)。
    params = request.query_params
    if "?" in url:
        url, _, target_query = url.partition("?")
        merged = dict(request.query_params)
        merged.update(httpx.QueryParams(target_query))
        params = merged
    resp = None
    try:
        resp = await cloud_client.send(
            cloud_client.build_request(
                request.method,
                url,
                headers=headers,
                content=request_data,
                params=params,
            ),
            stream=True,
        )
        if _detect_sse(resp):
            return StreamingResponse(
                _cloud_stream_wrapper(resp, path, db, model_anchor, request_start),
                status_code=resp.status_code,
                headers=_strip_headers(resp.headers, extra=("connection", "content-encoding")),
            )
        try:
            content = await resp.aread()
        finally:
            await resp.aclose()
        status_code = resp.status_code
        resp_headers = _strip_headers(resp.headers, extra=("connection", "content-encoding"))
        resp = None
        await _record_usage(
            db, model_anchor, path, content, request_start, time.time(), source="cloud"
        )
        return Response(content=content, status_code=status_code, headers=resp_headers)
    except HTTPException:
        raise
    except httpx.HTTPError as e:
        if resp is not None:
            await resp.aclose()
        logger.warning("cloud upstream error provider=%s: %s", provider_name, e)
        raise HTTPException(502, f"upstream error: {e}")
    except Exception as e:  # noqa: BLE001
        if resp is not None:
            await resp.aclose()
        logger.warning("cloud internal provider=%s: %s", provider_name, e)
        raise HTTPException(500, f"internal: {e}")


def _reject_absolute_url_path(path: str) -> None:
    """SSRF 守卫:catch-all {path:path} 剥前导 /,故请求 /http://evil.com/x 到达时
    path 恰为绝对 URL,httpx build_request 对绝对 URL 原样外发(绕开 base_url)。
    含 :// 的一律 400(相对子路径/空路径不含,照常转发)。"""
    if "://" in path:
        raise HTTPException(400, "absolute URLs not allowed in proxy path")


async def forward(
    request: Request,
    path: str,
    lifecycle,
    cfg,
    db,
    client_pool,
    cloud_client: httpx.AsyncClient | None = None,
) -> Response:
    _reject_absolute_url_path(path)
    from llm_manager import state
    from llm_manager.state import ModelStatus

    t0 = time.monotonic()
    body = await _read_body(request)
    alias = _extract_model_alias(body)

    # ---- 二段分派:云端优先(model 先行)----
    if alias:
        cloud_hit = resolve_cloud_model(cfg, alias)
        if cloud_hit is not None:
            return await forward_cloud(
                request, path, cfg, db, cloud_client, cloud_hit[0], cloud_hit[1], cloud_hit[2], body
            )
        parsed = parse_cloud_id(alias)
        if parsed is not None:
            p = cfg.cloud_providers.get(parsed[0])
            if p is not None and not p.enabled:
                # 已知服务商被禁用:目录外模型名也归云端流 → 503(forward_cloud 首检)
                return await forward_cloud(
                    request, path, cfg, db, cloud_client, parsed[0], p, None, body
                )
    else:
        mapping = resolve_global_mapping(cfg, path)
        if mapping is not None:
            return await forward_cloud(
                request, path, cfg, db, cloud_client, mapping[0].name, mapping[0], None, body
            )

    # ---- 本地分支(现状零改动)----
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
                _stream_wrapper(
                    resp, path, primary, db, request_start, guard=_StreamGuard(request, primary)
                ),
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
        cloud_client = getattr(request.app.state, "cloud_client", None)
        return await forward(
            request, path, lifecycle, cfg, db, client_pool, cloud_client=cloud_client
        )

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
