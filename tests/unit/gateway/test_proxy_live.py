"""代理旁路抓取与双向取消的测试。

沿用 `test_proxy.py` 的测试形状(MockTransport / FakeLifecycle / _make_request),
但全部走 ``registry`` 参数 —— 不传 registry 时旧行为必须逐字节不变(由 test_proxy.py 守)。
"""

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from fastapi import HTTPException

from llm_manager import state
from llm_manager.config import AppConfig, Command, ModelConfig, ProgramConfig, Scheme
from llm_manager.data.persistence import open_db
from llm_manager.gateway import proxy
from llm_manager.gateway.live_requests import RequestRegistry, RequestStatus
from llm_manager.gateway.stream_capture import StreamTextExtractor
from llm_manager.state import ModelStatus


def _cfg() -> AppConfig:
    m = ModelConfig(
        aliases=("m1", "alias1"),
        mode="Chat",
        port=8000,
        schemes={
            "s": Scheme("s", frozenset({"rtx 4060"}), Command(exe="r.cmd"), {"rtx 4060": 2048})
        },
    )
    return AppConfig(
        program=ProgramConfig(host="127.0.0.1", port=8080, alive_time=60, log_level="INFO"),
        models={"m1": m},
        wol=None,
        claude_configs={},
    )


def _make_request(method, path, json_body, content_type="application/json"):
    from starlette.requests import Request as StarletteRequest

    body = json.dumps(json_body).encode() if json_body is not None else b""
    scope = {
        "type": "http",
        "method": method,
        "path": path.split("/"),
        "raw_path": path.encode(),
        "query_string": b"",
        "headers": [(b"content-type", content_type.encode()), (b"host", b"x")]
        + ([(b"content-length", str(len(body)).encode())] if body else []),
    }
    seen = {"body": False}

    async def receive():
        # 首读给 body;之后挂住 —— 模拟"连接仍然活着"。真 uvicorn 的 receive 会阻塞,
        # 若这里恒返 http.request 会让 _StreamGuard 空转把事件循环饿死。
        if not seen["body"]:
            seen["body"] = True
            return {"type": "http.request", "body": body, "more_body": False}
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    return StarletteRequest(scope, receive)


class FakeLifecycle:
    def __init__(self, status=None):
        self._status = status if status is not None else ModelStatus.ROUTING

    async def ensure_running(self, alias, *, inc_pending=False):
        if inc_pending and self._status == ModelStatus.ROUTING:
            state.begin_request(alias)
        return self._status


def _registry() -> RequestRegistry:
    return RequestRegistry(retention=lambda: 20)


def _sse(*payloads: dict) -> bytes:
    return "".join(f"data: {json.dumps(p)}\n\n" for p in payloads).encode()


class FakeStreamResp:
    """可控的 SSE 上游:逐块产出,可被 aclose 打断。"""

    headers = {"content-type": "text/event-stream"}  # noqa: RUF012 — 测试桩

    def __init__(self, chunks: list[bytes], *, hang: bool = False) -> None:
        self._chunks = chunks
        self._hang = hang
        self.closed = False
        self._stop = asyncio.Event()

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c
        if self._hang:
            await self._stop.wait()
            raise RuntimeError("upstream aborted")

    async def aclose(self):
        self.closed = True
        self._stop.set()


async def _collect(gen) -> list[bytes]:
    return [c async for c in gen]


async def test_stream_capture_does_not_change_client_bytes():
    state._reset()
    reg = _registry()
    live = reg.start(
        model="m1",
        alias="m1",
        method="POST",
        path="/v1/chat/completions",
        stream=True,
        request_preview="hi",
    )
    chunks = [_sse({"choices": [{"delta": {"content": "你"}}]}), b"data: [DONE]\n\n"]
    resp = FakeStreamResp(chunks)
    db = open_db(Path(":memory:"))
    cap = StreamTextExtractor("v1/chat/completions")

    out = await _collect(
        proxy._stream_wrapper(resp, "v1/chat/completions", "m1", db, 1.0, live=live, capture=cap)
    )

    assert out == chunks  # 客户端字节逐块一致
    assert live.info.status is RequestStatus.COMPLETED
    assert live.text == "你"


async def test_request_body_is_pre_rewrite_and_sanitized():
    state._reset()
    reg = _registry()
    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True})),
    )
    db = open_db(Path(":memory:"))
    body = {
        "model": "alias1",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "看图"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
                ],
            }
        ],
    }
    req = _make_request("POST", "v1/chat/completions", body)
    await proxy.forward(
        req, "v1/chat/completions", FakeLifecycle(), _cfg(), db, {8000: client}, registry=reg
    )

    snap = reg.snapshot()
    assert len(snap) == 1
    info = snap[0]
    assert info.request_body is not None
    assert info.request_body["model"] == "alias1"  # 客户端原文,不是 served name
    assert "stream_options" not in info.request_body
    assert "base64" not in json.dumps(info.request_body, ensure_ascii=False)
    assert info.request_preview == "看图"
    await client.aclose()


async def test_finalize_does_not_run_in_forward_finally():
    """forward 的 finally 在 return StreamingResponse 时就跑了 —— 此时记录必须还在跑。"""
    state._reset()
    reg = _registry()
    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=_sse({"choices": [{"delta": {"content": "x"}}]}),
            )
        ),
    )
    db = open_db(Path(":memory:"))
    req = _make_request(
        "POST", "v1/chat/completions", {"model": "m1", "stream": True, "messages": []}
    )
    resp = await proxy.forward(
        req, "v1/chat/completions", FakeLifecycle(), _cfg(), db, {8000: client}, registry=reg
    )
    assert resp.status_code == 200
    info = reg.snapshot()[0]
    assert info.status is RequestStatus.RUNNING
    await client.aclose()


async def test_terminal_state_written_before_first_await_on_cancel():
    """任务被取消时 finally 里第一个 await 立刻抛 CancelledError —— 终态必须已经写好。"""
    state._reset()
    reg = _registry()
    live = reg.start(
        model="m1",
        alias="m1",
        method="POST",
        path="/v1/chat/completions",
        stream=True,
        request_preview="hi",
    )
    resp = FakeStreamResp([], hang=True)
    db = open_db(Path(":memory:"))
    cap = StreamTextExtractor("v1/chat/completions")

    gen = proxy._stream_wrapper(resp, "v1/chat/completions", "m1", db, 1.0, live=live, capture=cap)
    task = asyncio.create_task(_collect(gen))
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert live.info.status in {RequestStatus.COMPLETED, RequestStatus.FAILED}


async def test_cancel_closes_upstream_and_keeps_model_state():
    state._reset()
    state.set_status("m1", ModelStatus.ROUTING, force=True)
    reg = _registry()
    live = reg.start(
        model="m1",
        alias="m1",
        method="POST",
        path="/v1/chat/completions",
        stream=True,
        request_preview="hi",
    )
    resp = FakeStreamResp([_sse({"choices": [{"delta": {"content": "a"}}]})], hang=True)
    live.attach_upstream(resp)
    db = open_db(Path(":memory:"))
    cap = StreamTextExtractor("v1/chat/completions")

    gen = proxy._stream_wrapper(resp, "v1/chat/completions", "m1", db, 1.0, live=live, capture=cap)
    task = asyncio.create_task(_collect(gen))
    await asyncio.sleep(0)
    assert await reg.cancel(live.info.id) is True
    with pytest.raises(RuntimeError):
        await task

    assert resp.closed is True
    assert live.info.status is RequestStatus.CANCELLED
    assert state.get_status("m1") is ModelStatus.ROUTING  # 模型没被动


async def test_disconnect_marks_client_closed():
    state._reset()
    state.set_status("m1", ModelStatus.ROUTING, force=True)
    state.begin_request("m1")
    reg = _registry()
    live = reg.start(
        model="m1",
        alias="m1",
        method="POST",
        path="/v1/chat/completions",
        stream=True,
        request_preview="hi",
    )

    class DisconnectingRequest:
        async def receive(self):
            return {"type": "http.disconnect"}

    proxy._StreamGuard(DisconnectingRequest(), "m1", live=live)
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    assert live.info.status is RequestStatus.CLIENT_CLOSED
    assert state.pending_count("m1") == 0


async def test_pre_header_cancel_returns_499():
    state._reset()
    reg = _registry()

    async def slow_handler(request):
        await asyncio.sleep(5)
        return httpx.Response(200, json={"ok": True})

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(slow_handler)
    )
    db = open_db(Path(":memory:"))
    req = _make_request("POST", "v1/chat/completions", {"model": "m1", "messages": []})

    async def canceller():
        while not reg.snapshot():
            await asyncio.sleep(0)
        rid = reg.snapshot()[0].id
        await reg.cancel(rid)

    task = asyncio.create_task(canceller())
    with pytest.raises(HTTPException) as ei:
        await proxy.forward(
            req, "v1/chat/completions", FakeLifecycle(), _cfg(), db, {8000: client}, registry=reg
        )
    assert ei.value.status_code == 499
    await task
    assert reg.snapshot()[0].status is RequestStatus.CANCELLED
    await client.aclose()


async def test_upstream_http_error_marks_failed():
    state._reset()
    reg = _registry()
    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom")),
    )
    db = open_db(Path(":memory:"))
    req = _make_request("POST", "v1/chat/completions", {"model": "m1", "messages": []})
    resp = await proxy.forward(
        req, "v1/chat/completions", FakeLifecycle(), _cfg(), db, {8000: client}, registry=reg
    )
    assert resp.status_code == 500
    info = reg.snapshot()[0]
    assert info.status is RequestStatus.FAILED
    assert info.http_status == 500
    await client.aclose()


async def test_sanitize_failure_does_not_break_forwarding(monkeypatch):
    """抓取/脱敏抛错绝不能变成 500 —— 客户端必须照常拿到上游响应。"""
    state._reset()
    reg = _registry()
    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True})),
    )
    db = open_db(Path(":memory:"))
    req = _make_request("POST", "v1/chat/completions", {"model": "m1", "messages": []})

    def boom(_body):
        raise RecursionError("too deep")

    monkeypatch.setattr(proxy, "sanitize_body", boom)
    resp = await proxy.forward(
        req, "v1/chat/completions", FakeLifecycle(), _cfg(), db, {8000: client}, registry=reg
    )
    assert resp.status_code == 200
    assert json.loads(resp.body) == {"ok": True}
    assert reg.snapshot() == []  # 抓取放弃,不影响转发
    await client.aclose()


async def test_capture_exception_does_not_break_passthrough(monkeypatch):
    state._reset()
    reg = _registry()
    live = reg.start(
        model="m1",
        alias="m1",
        method="POST",
        path="/v1/chat/completions",
        stream=True,
        request_preview="hi",
    )
    chunks = [_sse({"choices": [{"delta": {"content": "x"}}]})]
    resp = FakeStreamResp(chunks)
    db = open_db(Path(":memory:"))

    class ExplodingCapture:
        def feed(self, chunk):
            raise RuntimeError("capture boom")

        def flush(self):
            raise RuntimeError("capture boom")

        finish_reason = None
        has_reasoning = False
        has_tool_calls = False

    out = await _collect(
        proxy._stream_wrapper(
            resp, "v1/chat/completions", "m1", db, 1.0, live=live, capture=ExplodingCapture()
        )
    )
    assert out == chunks
    assert live.info.status is RequestStatus.COMPLETED
