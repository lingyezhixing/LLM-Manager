"""Synchronous in-process event bus (plumbing)."""

from __future__ import annotations

import threading

from llm_manager.domain.records import LifecycleEvent
from llm_manager.ports.events import EventHandler, Subscription


class EventBusImpl:
    """Thread-safe pub/sub for LifecycleEvent."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._handlers: list[EventHandler] = []

    def publish(self, event: LifecycleEvent) -> None:
        with self._lock:
            handlers = list(self._handlers)
        for handler in handlers:
            handler(event)

    def subscribe(self, handler: EventHandler) -> Subscription:
        with self._lock:
            self._handlers.append(handler)

        def _cancel() -> None:
            with self._lock:
                if handler in self._handlers:
                    self._handlers.remove(handler)

        return Subscription(cancel=_cancel)
