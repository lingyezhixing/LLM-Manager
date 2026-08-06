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
from llm_manager.gateway.aliases import resolve_alias_checked
from llm_manager.state import ModelStatus


def _usage_rows(db, name="m1"):
    """model_requests JOIN models 按 original_name 直查(测试用;与已删 fetch_usage 查询同构)。"""
    return db.conn.execute(
        "SELECT input_tokens FROM model_requests r JOIN models m ON r.model_id = m.id "
        "WHERE m.original_name = ?",
        (name,),
    ).fetchall()


def _cfg():
    m = ModelConfig(
        primary_name="m1",
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


# ---------- helpers ----------
def test_strip_headers_removes_hop_by_hop():
    out = proxy._strip_headers(
        {
            "host": "x",
            "content-length": "3",
            "transfer-encoding": "chunked",
            "authorization": "Bearer t",
        },
        extra=("host",),
    )
    assert "host" not in out and "content-length" not in out and "transfer-encoding" not in out
    assert out["authorization"] == "Bearer t"


def test_strip_headers_response_side_drops_length_encoding():
    out = proxy._strip_headers(
        {
            "content-length": "9",
            "content-encoding": "gzip",
            "transfer-encoding": "chunked",
            "connection": "keep-alive",
            "content-type": "application/json",
        },
        extra=("connection", "content-encoding"),
    )
    for bad in ("content-length", "content-encoding", "transfer-encoding", "connection"):
        assert bad not in out
    assert out["content-type"] == "application/json"


def test_detect_sse_by_content_type():
    class R:
        headers = {"content-type": "text/event-stream"}  # noqa: RUF012 — 测试桩,类属性只读

    assert proxy._detect_sse(R()) is True

    class R2:
        headers = {"content-type": "application/json"}  # noqa: RUF012 — 测试桩,类属性只读

    assert proxy._detect_sse(R2()) is False


def test_extract_model_alias_from_dict():
    assert proxy._extract_model_alias({"model": "m1"}) == "m1"
    assert proxy._extract_model_alias(b"raw") is None
    assert proxy._extract_model_alias({}) is None


def test_is_stream_flag():
    assert proxy._is_stream({"stream": True}) is True
    assert proxy._is_stream({"stream": False}) is False
    assert proxy._is_stream({}) is False
    assert proxy._is_stream(b"raw") is False


def test_reserialize_roundtrip():
    body = {"model": "m1", "stream": True}
    assert json.loads(proxy._reserialize(body)) == body


# ---------- resolve_alias_checked / _get_or_create_client ----------
def test_resolve_alias_unknown_raises_404():
    with pytest.raises(HTTPException) as ei:
        resolve_alias_checked(_cfg(), "nope")
    assert ei.value.status_code == 404


def test_resolve_alias_missing_model_raises_400():
    with pytest.raises(HTTPException) as ei:
        resolve_alias_checked(_cfg(), None)
    assert ei.value.status_code == 400


def test_resolve_alias_normal():
    assert resolve_alias_checked(_cfg(), "alias1") == "m1"


def test_get_or_create_client_lazy_and_reuse():
    pool: dict = {}
    c1 = proxy._get_or_create_client(pool, 8000)
    c2 = proxy._get_or_create_client(pool, 8000)
    assert c1 is c2 and 8000 in pool
    asyncio.run(c1.aclose())


# ---------- _inject_include_usage ----------
def test_inject_include_usage_when_needed():
    out = proxy._inject_include_usage({"model": "m1", "stream": True}, "v1/chat/completions")
    assert out["stream_options"]["include_usage"] is True


def test_inject_include_usage_preserves_existing_stream_options():
    out = proxy._inject_include_usage(
        {"model": "m1", "stream": True, "stream_options": {"foo": 1}}, "v1/chat/completions"
    )
    assert out["stream_options"]["include_usage"] is True
    assert out["stream_options"]["foo"] == 1


def test_inject_include_usage_skips_non_usage_path():
    out = proxy._inject_include_usage({"model": "m1", "stream": True}, "v1/embeddings")
    assert "stream_options" not in out


# ---------- _record_usage ----------
def test_record_usage_writes_row():
    async def main():
        db = open_db(Path(":memory:"))
        body = b'{"usage":{"prompt_tokens":5,"completion_tokens":10,"total_tokens":15}}'
        await proxy._record_usage(db, "m1", "v1/chat/completions", body, 1.0, 2.0)
        rows = _usage_rows(db)
        assert len(rows) == 1 and rows[0]["input_tokens"] == 5

    asyncio.run(main())


def test_record_usage_no_usage_no_row():
    async def main():
        db = open_db(Path(":memory:"))
        body = b'{"choices":[{"message":{"content":"hi"}}]}'
        await proxy._record_usage(db, "m1", "v1/chat/completions", body, 1.0, 2.0)
        assert len(_usage_rows(db)) == 0

    asyncio.run(main())


def test_record_usage_best_effort_swallows_exception(monkeypatch):
    import sqlite3

    from llm_manager.data import usage

    async def main():
        db = open_db(Path(":memory:"))

        def boom(*a, **kw):
            raise sqlite3.OperationalError("disk full")

        monkeypatch.setattr(usage, "record_usage", boom)
        body = b'{"usage":{"prompt_tokens":5,"completion_tokens":10}}'
        await proxy._record_usage(db, "m1", "v1/chat/completions", body, 1.0, 2.0)  # 不抛

    asyncio.run(main())


# ---------- _stream_wrapper ----------
def test_stream_sample_keeps_head_and_tail_drops_middle():
    s = proxy._StreamSample(head_max=16, tail_max=16)
    s.feed(b"H" * 20)  # head 截到 16
    s.feed(b"M" * 100)  # 中间全丢
    s.feed(b"T" * 20)  # tail 截到 16
    out = s.sample()
    assert out.startswith(b"H" * 16)
    assert out.endswith(b"T" * 16)
    assert b"M" not in out
    assert len(out) == 32


def test_stream_sample_small_stream_returns_head_only_no_dup():
    s = proxy._StreamSample(head_max=64, tail_max=64)
    s.feed(b"abcdef")
    assert s.sample() == b"abcdef"  # 全流 < head → 不拼接 tail(无重复)


async def test_stream_wrapper_long_stream_parses_usage_from_head_and_tail():
    """长流(中间远超 head+tail)中间被丢弃,但头部 message_start(input)+ 尾部
    message_delta(output)仍在 → metering 解析用量正确(头尾双缓冲契约)。"""
    state._reset()
    state.set_status("m1", ModelStatus.ROUTING, force=True)
    state.begin_request("m1")
    head = (
        b'event: message_start\ndata: {"type":"message_start","message":'
        b'{"usage":{"input_tokens":42,"cache_read_input_tokens":0,'
        b'"cache_creation_input_tokens":0}}}\n\n'
    )
    middle = (
        b'event: content_block_delta\ndata: {"type":"content_block_delta","delta":{"text":"x"}}\n\n'
    ) * 4000  # ~340KB ≫ head(16K)+tail(128K)
    tail = b'event: message_delta\ndata: {"type":"message_delta","usage":{"output_tokens":99}}\n\n'

    class FakeResp:
        headers = {"content-type": "text/event-stream"}  # noqa: RUF012 — 测试桩,类属性只读

        async def aiter_bytes(self):
            for c in [head, middle, tail]:
                yield c

        async def aclose(self):
            pass

    db = open_db(Path(":memory:"))
    out = [c async for c in proxy._stream_wrapper(FakeResp(), "v1/messages", "m1", db, 1.0)]
    assert b"".join(out) == head + middle + tail  # 透传完整(不截断客户端流)
    assert len(_usage_rows(db)) == 1
    row = db.conn.execute(
        "SELECT input_tokens, output_tokens FROM model_requests r JOIN models m ON r.model_id = m.id "
        "WHERE m.original_name = ?",
        ("m1",),
    ).fetchone()
    assert row["input_tokens"] == 42
    assert row["output_tokens"] == 99


async def test_stream_wrapper_forwards_chunks_records_usage_ends_request():
    state._reset()
    state.set_status("m1", ModelStatus.ROUTING, force=True)
    state.begin_request("m1")

    class FakeResp:
        headers = {"content-type": "text/event-stream"}  # noqa: RUF012 — 测试桩,类属性只读

        async def aiter_bytes(self):
            for c in [
                b'data: {"choices":[]}\n\n',
                b'data: {"usage":{"prompt_tokens":3,"completion_tokens":7}}\n\n',
            ]:
                yield c

        async def aclose(self):
            pass

    db = open_db(Path(":memory:"))
    out = [c async for c in proxy._stream_wrapper(FakeResp(), "v1/chat/completions", "m1", db, 1.0)]
    assert len(out) == 2
    rows = _usage_rows(db)
    assert len(rows) == 1 and rows[0]["input_tokens"] == 3
    assert state.pending_count("m1") == 0


# ---------- forward ----------
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

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return StarletteRequest(scope, receive)


class FakeLifecycle:
    def __init__(self, status=None):
        self._status = status if status is not None else ModelStatus.ROUTING

    async def ensure_running(self, alias, *, inc_pending=False):
        if inc_pending and self._status == ModelStatus.ROUTING:
            state.begin_request(alias)  # mimic 真实 ensure_running:返回 ROUTING 前原子 inc pending
        return self._status


async def test_forward_non_stream_records_usage_and_ends_request():
    state._reset()

    def handler(req):
        return httpx.Response(
            200,
            json={
                "id": "x",
                "usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
            },
            headers={"content-type": "application/json"},
        )

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(handler)
    )
    db = open_db(Path(":memory:"))
    req = _make_request("POST", "v1/chat/completions", {"model": "m1", "stream": False})
    resp = await proxy.forward(
        req, "v1/chat/completions", FakeLifecycle(), _cfg(), db, {8000: client}
    )
    assert resp.status_code == 200
    rows = _usage_rows(db)
    assert len(rows) == 1 and rows[0]["input_tokens"] == 4
    assert state.pending_count("m1") == 0
    await client.aclose()


async def test_forward_stream_returns_streaming_and_records_on_consume():
    state._reset()
    sse = b'data: {"usage":{"prompt_tokens":2,"completion_tokens":3}}\n\n'

    def handler(req):
        return httpx.Response(200, content=sse, headers={"content-type": "text/event-stream"})

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(handler)
    )
    db = open_db(Path(":memory:"))
    req = _make_request("POST", "v1/chat/completions", {"model": "m1", "stream": True})
    resp = await proxy.forward(
        req, "v1/chat/completions", FakeLifecycle(), _cfg(), db, {8000: client}
    )
    assert resp.status_code == 200
    consumed = b"".join([chunk async for chunk in resp.body_iterator])
    assert b"usage" in consumed
    rows = _usage_rows(db)
    assert len(rows) == 1
    assert state.pending_count("m1") == 0
    await client.aclose()


async def test_forward_ensure_running_failed_returns_503():
    state._reset()
    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )
    db = open_db(Path(":memory:"))
    req = _make_request("POST", "v1/chat/completions", {"model": "m1"})
    with pytest.raises(HTTPException) as ei:
        await proxy.forward(
            req,
            "v1/chat/completions",
            FakeLifecycle(ModelStatus.FAILED),
            _cfg(),
            db,
            {8000: client},
        )
    assert ei.value.status_code == 503
    assert state.pending_count("m1") == 0
    await client.aclose()


async def test_forward_alias_unknown_returns_404():
    state._reset()
    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )
    db = open_db(Path(":memory:"))
    req = _make_request("POST", "v1/chat/completions", {"model": "nope"})
    with pytest.raises(HTTPException) as ei:
        await proxy.forward(req, "v1/chat/completions", FakeLifecycle(), _cfg(), db, {8000: client})
    assert ei.value.status_code == 404
    await client.aclose()


async def test_forward_missing_model_returns_400():
    state._reset()
    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8000",
        transport=httpx.MockTransport(lambda r: httpx.Response(200)),
    )
    db = open_db(Path(":memory:"))
    req = _make_request("POST", "v1/chat/completions", {"messages": []})  # 无 model
    with pytest.raises(HTTPException) as ei:
        await proxy.forward(req, "v1/chat/completions", FakeLifecycle(), _cfg(), db, {8000: client})
    assert ei.value.status_code == 400
    assert state.pending_count("m1") == 0  # 400 raise 在 begin_request 前
    await client.aclose()


async def test_forward_upstream_5xx_passes_through_raw():
    state._reset()
    raw = b'{"error":{"message":"model overloaded","type":"server_error"}}'

    def handler(req):
        return httpx.Response(503, content=raw, headers={"content-type": "application/json"})

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(handler)
    )
    db = open_db(Path(":memory:"))
    req = _make_request("POST", "v1/chat/completions", {"model": "m1"})
    resp = await proxy.forward(
        req, "v1/chat/completions", FakeLifecycle(), _cfg(), db, {8000: client}
    )
    assert resp.status_code == 503
    assert resp.body == raw
    assert b'"detail"' not in resp.body
    assert state.pending_count("m1") == 0
    await client.aclose()


async def test_forward_record_usage_failure_does_not_pollute_passthrough(monkeypatch):
    import sqlite3

    from llm_manager.data import usage

    state._reset()

    def boom(*a, **kw):
        raise sqlite3.OperationalError("boom")

    monkeypatch.setattr(usage, "record_usage", boom)

    def handler(req):
        return httpx.Response(
            200,
            json={
                "id": "x",
                "usage": {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10},
            },
            headers={"content-type": "application/json"},
        )

    client = httpx.AsyncClient(
        base_url="http://127.0.0.1:8000", transport=httpx.MockTransport(handler)
    )
    db = open_db(Path(":memory:"))
    req = _make_request("POST", "v1/chat/completions", {"model": "m1"})
    resp = await proxy.forward(
        req, "v1/chat/completions", FakeLifecycle(), _cfg(), db, {8000: client}
    )
    assert resp.status_code == 200  # 透传,非 500
    assert state.pending_count("m1") == 0
    await client.aclose()
