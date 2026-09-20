import time

import httpx

from llm_manager.runtime import probes
from llm_manager.runtime.probes import ProbeResult, _deep_request, probe_registry


def test_registry_has_all_three_modes():
    assert set(probe_registry) == {"Chat", "Embedding", "Reranker"}


def test_deep_request_shape_per_mode():
    assert _deep_request("Chat")[0] == "/chat/completions"
    assert "messages" in _deep_request("Chat")[1]
    assert _deep_request("Embedding")[0] == "/embeddings"
    assert _deep_request("Reranker")[0] == "/rerank"


def test_probe_chat_success_via_mock_transport(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/models":
            return httpx.Response(200, json={"data": []})
        return httpx.Response(200, json={})

    client = httpx.Client(
        base_url="http://127.0.0.1:9999/v1", transport=httpx.MockTransport(handler)
    )
    monkeypatch.setattr(probes, "_make_client", lambda port: client)
    result = probes.probe_chat("alias", 9999, timeout=10.0)
    assert isinstance(result, ProbeResult)
    assert result.ok is True
    client.close()


def test_probe_returns_failure_when_shallow_never_succeeds(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    client = httpx.Client(
        base_url="http://127.0.0.1:9999/v1", transport=httpx.MockTransport(handler)
    )
    monkeypatch.setattr(probes, "_make_client", lambda port: client)
    result = probes.probe_chat("alias", 9999, timeout=0.5)
    assert result.ok is False
    client.close()


def test_probe_fails_fast_when_process_dead(monkeypatch):
    """进程已死(is_alive 返回 False)→ probe 首轮即快速失败,不空转到 timeout。
    回归:启动瞬间崩溃时 probe 曾空转 startup_timeout,卡死 inflight future。"""
    calls = 0

    def is_alive() -> bool:
        nonlocal calls
        calls += 1
        return False

    client = httpx.Client(
        base_url="http://127.0.0.1:9999/v1",
        transport=httpx.MockTransport(lambda r: httpx.Response(503)),
    )
    monkeypatch.setattr(probes, "_make_client", lambda port: client)
    start = time.monotonic()
    result = probes.probe_chat("alias", 9999, timeout=60, is_alive=is_alive)
    assert result.ok is False
    assert calls == 1  # 首轮 liveness check 即返回
    assert time.monotonic() - start < 5  # 远小于 timeout=60
    client.close()
