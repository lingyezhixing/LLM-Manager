"""Persisted/internally-emitted records."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from llm_manager.domain.meter import TokenUsage


@dataclass(frozen=True, slots=True)
class RequestRecord:
    """One forwarded LLM call's token fact row (written via MeteringSink)."""

    model_name: str
    start_time: float
    end_time: float
    usage: TokenUsage


@dataclass(frozen=True, slots=True)
class Session:
    """A heartbeat-updated run interval (program or model)."""

    entity: str
    start_time: float
    end_time: float


class LifecycleKind(Enum):
    MODEL_STARTED = "model_started"
    MODEL_ROUTING = "model_routing"
    MODEL_FAILED = "model_failed"
    MODEL_EVICTED = "model_evicted"
    MODEL_STOPPED = "model_stopped"


@dataclass(frozen=True, slots=True)
class LifecycleEvent:
    kind: LifecycleKind
    primary_name: str
    timestamp: float
