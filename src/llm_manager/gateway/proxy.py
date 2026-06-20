"""GatewayImpl — the proxy. STUB in this plan: returns 501.
Real alias-resolution + ensure_running + httpx forwarding + two-point token
injection arrives in the gateway stub-fill phase (spec §16)."""

from __future__ import annotations

from llm_manager.ports.events import EventBus
from llm_manager.ports.gateway import ProxyRequest, ProxyResponse
from llm_manager.ports.metering import MeteringSink
from llm_manager.ports.runtime import ModelRuntimePort


class GatewayImpl:
    def __init__(self, *, runtime: ModelRuntimePort, meter: MeteringSink, events: EventBus) -> None:
        self._runtime = runtime
        self._meter = meter
        self._events = events

    async def forward(self, request: ProxyRequest) -> ProxyResponse:
        # TODO(phase-gateway): resolve alias -> primary -> port; ensure_running;
        # httpx streaming forward; inject stream_options.include_usage per
        # endpoint_shapes; record usage via self._meter on stream close + non-stream.
        return ProxyResponse(
            status_code=501,
            headers={"content-type": "application/json"},
            body=b"",
            is_stream=False,
        )
