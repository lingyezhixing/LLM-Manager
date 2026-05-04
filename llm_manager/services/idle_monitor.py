from __future__ import annotations

import asyncio
import logging
import time

from llm_manager.config.models import AppConfig
from llm_manager.container import Container
from llm_manager.schemas.model import ModelState
from llm_manager.services.base import BaseService
from llm_manager.services.model_manager import ModelManager

logger = logging.getLogger(__name__)


class IdleMonitor(BaseService):
    """空闲模型自动停止监控"""

    def __init__(self, container: Container):
        super().__init__(container)
        self._task: asyncio.Task | None = None
        self._running = False

    async def on_start(self) -> None:
        app_config = self._container.resolve(AppConfig)
        alive_time = app_config.program.alive_time
        if alive_time <= 0:
            logger.info("Idle monitor disabled (alive_time=%d)", alive_time)
            return
        self._running = True
        self._task = asyncio.create_task(self._check_loop())
        logger.info("Idle monitor started (alive_time=%d min)", alive_time)

    async def on_stop(self) -> None:
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Idle monitor stopped")

    async def _check_loop(self) -> None:
        while self._running:
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                break

            if not self._running:
                break

            try:
                await self._check_idle_models()
            except Exception:
                logger.exception("Idle check failed")

    async def _check_idle_models(self) -> None:
        app_config = self._container.resolve(AppConfig)
        alive_time = app_config.program.alive_time
        if alive_time <= 0:
            return

        alive_sec = alive_time * 60
        now = time.time()

        model_manager = self._container.resolve(ModelManager)
        instances = model_manager.get_all_instances()

        for name, instance in instances.items():
            if instance.state != ModelState.RUNNING:
                continue
            if instance.last_request_at is None:
                continue

            idle_duration = now - instance.last_request_at
            if idle_duration > alive_sec:
                logger.info(
                    "Model '%s' idle for %.0f seconds (threshold: %d), stopping",
                    name, idle_duration, alive_sec,
                )
                try:
                    await model_manager.stop_model(name)
                except Exception:
                    logger.exception("Failed to auto-stop idle model '%s'", name)
