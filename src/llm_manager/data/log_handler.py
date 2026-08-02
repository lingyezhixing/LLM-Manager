"""System log handler: forwards logging records into the LogStore queue without
blocking the caller. Dropped records never affect the main program (the collector
is O(1) append; batching/persistence happen in the LogStore flush task).
Fed by app.py lifespan (install/remove) — tests and non-lifespan paths stay clean.
"""
from __future__ import annotations

import logging
from collections.abc import Callable


class SystemLogHandler(logging.Handler):
    """Synchronous handler → collector callable (``logs.capture_system``).
    Collector receives one ``(text, ts, levelname)`` tuple per record and must be
    non-blocking (in-memory append); app wiring adapts it via
    ``lambda item: capture_system(*item)``."""

    def __init__(self, collector: Callable[[tuple[str, float, str]], None]) -> None:
        super().__init__()
        self._collector = collector

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._collector((record.getMessage(), record.created, record.levelname))
        except Exception:
            pass   # 日志管道永不影响主程序
