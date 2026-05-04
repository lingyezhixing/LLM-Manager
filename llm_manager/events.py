from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

logger = logging.getLogger(__name__)


@dataclass
class Event:
    timestamp: float = field(default_factory=time.time)


class EventBus:
    def __init__(self):
        self._handlers: dict[type[Event], list[Callable]] = defaultdict(list)

    def subscribe(self, event_type: type[Event], handler: Callable) -> None:
        self._handlers[event_type].append(handler)

    def unsubscribe(self, event_type: type[Event], handler: Callable) -> None:
        handlers = self._handlers.get(event_type, [])
        if handler in handlers:
            handlers.remove(handler)

    async def publish(self, event: Event) -> None:
        handlers = self._handlers.get(type(event), [])
        if not handlers:
            return

        for handler in handlers:
            try:
                result = handler(event)
                if asyncio.iscoroutine(result):
                    await result
            except Exception:
                logger.exception("Event handler %s failed for %s", handler.__name__, type(event).__name__)
