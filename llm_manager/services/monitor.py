from __future__ import annotations

import asyncio
import logging

from llm_manager.services.base import BaseService
from llm_manager.container import Container

logger = logging.getLogger(__name__)


class MonitorService(BaseService):
    def __init__(self, container: Container):
        super().__init__(container)
        self._running = False
        self._task: asyncio.Task | None = None

    async def on_start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        logger.info("Monitor service started")

    async def on_stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Monitor service stopped")

    async def _monitor_loop(self):
        while self._running:
            try:
                await asyncio.sleep(10)
            except asyncio.CancelledError:
                break
