"""Gateway (proxy) port + request/response contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from llm_manager.registry import Registry


@dataclass(frozen=True, slots=True)
class EndpointShape:
    """Per-path proxy metadata. needs_include_usage replaces the hardcoded
    api_router.py:154 path list."""

    needs_include_usage: bool = False


@dataclass(frozen=True, slots=True)
class ProxyRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes
    query_params: dict[str, str]


@dataclass(frozen=True, slots=True)
class ProxyResponse:
    status_code: int
    headers: dict[str, str]
    body: bytes
    is_stream: bool = False


@runtime_checkable
class GatewayPort(Protocol):
    async def forward(self, request: ProxyRequest) -> ProxyResponse: ...


# Per-endpoint proxy metadata (spec §8). Populated by Plan 2 (metering/gateway).
endpoint_shapes: Registry[str, EndpointShape] = Registry()
