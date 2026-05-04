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
from llm_manager.plugins.base_interface import InterfacePlugin
from llm_manager.plugins.loader import PluginLoader
from llm_manager.plugins.registry import PluginRegistry
from llm_manager.database.repos.billing_repo import BillingRepository
from llm_manager.database.repos.model_repo import ModelRuntimeRepository, ProgramRuntimeRepository
from llm_manager.database.repos.request_repo import RequestRepository
from llm_manager.services.billing import BillingService
from llm_manager.services.idle_monitor import IdleMonitor
from llm_manager.services.device_monitor import DeviceMonitor
from llm_manager.services.model_manager import ModelManager
from llm_manager.services.monitor import MonitorService
from llm_manager.services.process_manager import ProcessManager
from llm_manager.services.request_router import RequestRouter
from llm_manager.services.token_tracker import TokenTracker
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

        container.register(ModelRuntimeRepository, ModelRuntimeRepository)
        container.register(ProgramRuntimeRepository, ProgramRuntimeRepository)
        container.register(RequestRepository, RequestRepository)
        container.register(BillingRepository, BillingRepository)

        container.register(ProcessManager, ProcessManager)
        container.register(DeviceMonitor, DeviceMonitor)
        container.register(ModelManager, ModelManager)
        container.register(TokenTracker, TokenTracker)
        container.register(RequestRouter, RequestRouter)
        container.register(BillingService, BillingService)
        container.register(MonitorService, MonitorService)
        container.register(IdleMonitor, IdleMonitor)

        return container

    def _load_plugins(self, config: AppConfig, container: Container) -> None:
        plugin_loader = PluginLoader()
        registry = container.resolve(PluginRegistry)

        self._load_device_plugins(config, plugin_loader, registry)
        self._load_interface_plugins(config, plugin_loader, registry)

    def _load_device_plugins(self, config: AppConfig, loader: PluginLoader, registry: PluginRegistry) -> None:
        from llm_manager.plugins.devices import CPUDevice, NvidiaDevice, AMDDevice

        needed: set[str] = set()
        for entry in config.models.values():
            for dep in entry.get_deployments().values():
                needed.update(dep.required_devices)

        for dev_name in needed:
            cls = self._resolve_device_class(dev_name, CPUDevice, NvidiaDevice, AMDDevice)
            if cls is None:
                logger.warning("Unknown device name '%s', skipping", dev_name)
                continue
            try:
                kwargs = {"device_name": dev_name} if cls is not CPUDevice else {}
                instance = loader.load(cls, **kwargs)
                errors = loader.validate(instance)
                if errors:
                    logger.warning("Device plugin '%s' validation errors: %s", dev_name, errors)
                    continue
                registry.register_device(instance)
                logger.info("Loaded device plugin: %s", instance.name)
            except Exception:
                logger.exception("Failed to load device plugin: %s", dev_name)

    @staticmethod
    def _resolve_device_class(dev_name: str, cpu_cls: type, nvidia_cls: type, amd_cls: type) -> type | None:
        name = dev_name.lower()
        if name == "cpu":
            return cpu_cls
        if any(kw in name for kw in ("rtx", "gtx", "geforce", "v100", "nvidia")):
            return nvidia_cls
        if "amd" in name:
            return amd_cls
        return None

    def _load_interface_plugins(self, config: AppConfig, loader: PluginLoader, registry: PluginRegistry) -> None:
        from llm_manager.plugins.interfaces import ChatInterface, EmbeddingInterface, RerankerInterface

        for cls in [ChatInterface, EmbeddingInterface, RerankerInterface]:
            try:
                instance = loader.load(cls)
                errors = loader.validate(instance)
                if errors:
                    logger.warning("Built-in interface plugin %s validation errors: %s", cls.__name__, errors)
                    continue
                registry.register_interface(instance)
                logger.info("Loaded interface plugin: %s", instance.name)
            except Exception:
                logger.exception("Failed to load interface plugin: %s", cls.__name__)

        self._discover_extension_plugins(
            loader, registry, config.program.interface_plugin_dir, InterfacePlugin, is_device=False,
        )

    def _discover_extension_plugins(
        self,
        loader: PluginLoader,
        registry: PluginRegistry,
        plugin_dir,
        base_class: type,
        *,
        is_device: bool,
    ) -> None:
        classes = loader.discover(plugin_dir, base_class)
        for cls in classes:
            try:
                instance = loader.load(cls)
                errors = loader.validate(instance)
                if errors:
                    logger.warning("Extension plugin %s validation errors: %s", cls.__name__, errors)
                    continue
                if is_device:
                    registry.register_device(instance)
                else:
                    registry.register_interface(instance)
                logger.info("Loaded extension plugin: %s", instance.name)
            except Exception:
                logger.exception("Failed to load extension plugin: %s", cls.__name__)

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
