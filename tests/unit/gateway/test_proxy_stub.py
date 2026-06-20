import pytest

from llm_manager.gateway.proxy import GatewayImpl
from llm_manager.ports.gateway import GatewayPort, ProxyRequest


class _FakeRuntime:
    async def ensure_running(self, primary): ...  # noqa: ARG002
    def start(self, primary): ...  # noqa: ARG002
    def stop(self, primary): ...  # noqa: ARG002
    def status(self, primary): ...  # noqa: ARG002
    def begin_request(self, primary): ...  # noqa: ARG002
    def end_request(self, primary): ...  # noqa: ARG002


class _FakeMeter:
    def record_usage(self, record): ...  # noqa: ARG002


class _FakeBus:
    def publish(self, event): ...  # noqa: ARG002
    def subscribe(self, handler): ...  # noqa: ARG002


@pytest.mark.asyncio
async def test_forward_is_a_gatewayport_and_returns_501_stub():
    gw = GatewayImpl(runtime=_FakeRuntime(), meter=_FakeMeter(), events=_FakeBus())
    assert isinstance(gw, GatewayPort)
    resp = await gw.forward(ProxyRequest("POST", "v1/chat/completions", {}, b"{}", {}))
    assert resp.status_code == 501
