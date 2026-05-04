from __future__ import annotations

import asyncio
import logging
import sys
import threading
from pathlib import Path

import uvicorn

from llm_manager.api.app import create_api_app
from llm_manager.config.loader import YamlConfigLoader
from llm_manager.config.models import AppConfig
from llm_manager.container import Container
from llm_manager.database.engine import DatabaseEngine
from llm_manager.events import EventBus
from llm_manager.plugins.base_device import DevicePlugin
from llm_manager.plugins.base_interface import InterfacePlugin
from llm_manager.plugins.loader import PluginLoader
from llm_manager.plugins.registry import PluginRegistry
from llm_manager.database.repos.billing_repo import BillingRepository
from llm_manager.database.repos.model_repo import ModelRepository, ProgramRepository
from llm_manager.database.repos.request_repo import RequestRepository
from llm_manager.services.billing import BillingService
from llm_manager.services.device_monitor import DeviceMonitor
from llm_manager.services.model_manager import ModelManager
from llm_manager.services.monitor import MonitorService
from llm_manager.services.process_manager import ProcessManager
from llm_manager.services.request_router import RequestRouter
from llm_manager.utils.logger import setup_logging

logger = logging.getLogger(__name__)


class Application:
    def __init__(self, config_path: str = "config.yaml"):
        self._config_path = Path(config_path)
        self._container: Container | None = None
        self._tray = None

    def run(self) -> None:
        try:
            asyncio.run(self._async_run())
        except KeyboardInterrupt:
            logger.info("Received keyboard interrupt")

    async def _async_run(self) -> None:
        config = self._load_config()
        setup_logging(level=config.program.log_level)

        logger.info("LLM-Manager starting...")
        logger.info("Python: %s", sys.version)
        logger.info("Platform: %s", sys.platform)

        container = self._create_container(config)
        self._container = container

        await container.start_all()

        self._load_plugins(config, container)

        model_mgr = container.resolve(ModelManager)
        models_to_start = [
            name for name, inst in model_mgr.get_all_instances().items()
            if inst.config.auto_start
        ]
        if models_to_start:
            asyncio.create_task(model_mgr.start_auto_start_models())

        app = create_api_app(container)
        server_config = uvicorn.Config(
            app,
            host=config.program.host,
            port=config.program.port,
            log_level=config.program.log_level.lower(),
        )
        server = uvicorn.Server(server_config)

        self._start_tray(container)

        try:
            await server.serve()
        except Exception:
            logger.exception("Server error")
        finally:
            await self._shutdown(container)

    def _load_config(self) -> AppConfig:
        loader = YamlConfigLoader()
        return loader.load(self._config_path)

    def _create_container(self, config: AppConfig) -> Container:
        container = Container()
        container.register_instance(Container, container)
        container.register_instance(AppConfig, config)

        container.register(EventBus, EventBus)
        container.register(PluginRegistry, PluginRegistry)
        container.register(DatabaseEngine, lambda: DatabaseEngine(config.program))

        container.register(ModelRepository, ModelRepository)
        container.register(ProgramRepository, ProgramRepository)
        container.register(RequestRepository, RequestRepository)
        container.register(BillingRepository, BillingRepository)

        container.register(ProcessManager, ProcessManager)
        container.register(DeviceMonitor, DeviceMonitor)
        container.register(ModelManager, ModelManager)
        container.register(RequestRouter, RequestRouter)
        container.register(BillingService, BillingService)
        container.register(MonitorService, MonitorService)

        return container

    def _load_plugins(self, config: AppConfig, container: Container) -> None:
        plugin_loader = PluginLoader()
        registry = container.resolve(PluginRegistry)

        device_classes = plugin_loader.discover(config.program.device_plugin_dir, DevicePlugin)
        for cls in device_classes:
            try:
                instance = plugin_loader.load(cls)
                errors = plugin_loader.validate(instance)
                if errors:
                    logger.warning("Plugin %s validation errors: %s", cls.__name__, errors)
                    continue
                registry.register_device(instance)
                logger.info("Loaded device plugin: %s", instance.name)
            except Exception:
                logger.exception("Failed to load device plugin: %s", cls.__name__)

        interface_classes = plugin_loader.discover(config.program.interface_plugin_dir, InterfacePlugin)
        for cls in interface_classes:
            try:
                instance = plugin_loader.load(cls)
                errors = plugin_loader.validate(instance)
                if errors:
                    logger.warning("Plugin %s validation errors: %s", cls.__name__, errors)
                    continue
                registry.register_interface(instance)
                logger.info("Loaded interface plugin: %s", instance.name)
            except Exception:
                logger.exception("Failed to load interface plugin: %s", cls.__name__)

    def _start_tray(self, container: Container) -> None:
        try:
            from llm_manager.tray import SystemTray
            self._tray = SystemTray(container)
            self._tray.set_exit_callback(lambda: None)

            if self._tray.is_headless:
                logger.info("Running in headless mode")
                return

            tray_thread = threading.Thread(target=self._tray.start_tray, daemon=True)
            tray_thread.start()
            logger.info("System tray started")
        except Exception:
            logger.info("System tray not available")

    async def _shutdown(self, container: Container) -> None:
        logger.info("Shutting down...")
        await container.stop_all()
        logger.info("LLM-Manager stopped")
