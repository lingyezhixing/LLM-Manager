"""ManagedServices: start/stop a group of Services, tolerating per-service failures
so one bad stop() cannot prevent the rest (and process exit) from completing."""

from __future__ import annotations

import logging

from llm_manager.ports.service import Service

logger = logging.getLogger(__name__)


class ManagedServices:
    def __init__(self, services: list[Service]) -> None:
        self._services = list(services)

    def start(self) -> None:
        for svc in self._services:
            svc.start()

    def stop(self, timeout_s: float = 10.0) -> None:  # noqa: ARG002
        for svc in self._services:
            try:
                svc.stop()
            except Exception:  # noqa: BLE001
                logger.exception("service stop failed: %r", svc)
