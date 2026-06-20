"""Observer seam for lifecycle transitions."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from llm_manager.domain.records import LifecycleEvent

EventHandler = Callable[[LifecycleEvent], None]


@dataclass(frozen=True, slots=True)
class Subscription:
    """Handle returned by EventBus.subscribe; call .cancel() to unsubscribe."""

    cancel: Callable[[], None]


@runtime_checkable
class EventBus(Protocol):
    def publish(self, event: LifecycleEvent) -> None: ...

    def subscribe(self, handler: EventHandler) -> Subscription: ...
