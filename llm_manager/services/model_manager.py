from __future__ import annotations

import logging
import threading
import time

from llm_manager.config.models import AppConfig, ModelConfigEntry
from llm_manager.events import EventBus
from llm_manager.schemas.model import ModelInstance, ModelState
from llm_manager.plugins.registry import PluginRegistry
from llm_manager.services.base import BaseService
from llm_manager.container import Container
from llm_manager.services.device_monitor import DeviceMonitor
from llm_manager.services.process_manager import ProcessManager

logger = logging.getLogger(__name__)


class ModelManager(BaseService):
    def __init__(self, container: Container):
        super().__init__(container)
        self._instances: dict[str, ModelInstance] = {}
        self._lock = threading.RLock()
        self._app_config: AppConfig | None = None

    async def on_start(self) -> None:
        self._app_config = self._container.resolve(AppConfig)
        self._build_instances()

    def _build_instances(self):
        for name, entry in self._app_config.models.items():
            config = self._entry_to_config(name, entry)
            self._instances[name] = ModelInstance(name=name, config=config)

    def _entry_to_config(self, name: str, entry: ModelConfigEntry):
        from llm_manager.schemas.model import DeploymentConfig

        deployments = {}
        for dep_name, dep_data in entry.get_deployments().items():
            deployments[dep_name] = DeploymentConfig(
                required_devices=dep_data.required_devices,
                script_path=dep_data.script_path,
                memory_mb=dep_data.memory_mb,
            )

        from llm_manager.schemas.model import ModelConfig as MC
        return MC(
            name=name,
            aliases=entry.aliases,
            mode=entry.mode,
            port=entry.port,
            auto_start=entry.auto_start,
            deployments=deployments,
        )

    def get_instance(self, name: str) -> ModelInstance | None:
        return self._instances.get(name)

    def get_all_instances(self) -> dict[str, ModelInstance]:
        return dict(self._instances)

    def resolve_model_name(self, name_or_alias: str) -> str | None:
        if name_or_alias in self._instances:
            return name_or_alias
        for name, instance in self._instances.items():
            if name_or_alias in instance.config.aliases:
                return name
        return None

    async def start_model(self, name: str, deployment_name: str | None = None) -> ModelInstance:
        with self._lock:
            instance = self._instances.get(name)
            if instance is None:
                raise ValueError(f"Model '{name}' not found")
            if instance.state == ModelState.RUNNING:
                return instance
            if instance.state == ModelState.STARTING:
                raise RuntimeError(f"Model '{name}' is already starting")

        config = instance.config
        dep_name = deployment_name or next(iter(config.deployments), None)
        if dep_name is None:
            raise ValueError(f"No deployment available for model '{name}'")

        deployment = config.deployments[dep_name]

        device_svc = self._container.resolve(DeviceMonitor)
        if not device_svc.check_devices_available(deployment.required_devices):
            raise RuntimeError(
                f"Required devices not available: {deployment.required_devices}"
            )

        with self._lock:
            instance.state = ModelState.STARTING
            instance.active_deployment = dep_name

        try:
            process_svc = self._container.resolve(ProcessManager)
            process_info = await process_svc.start_process(
                name=name,
                script_path=str(deployment.script_path),
            )
            instance.pid = process_info.pid
            instance.started_at = time.time()

            interface_plugin = self._container.resolve(PluginRegistry).get_interface(config.mode)
            if interface_plugin:
                healthy = await interface_plugin.health_check(config.port)
                if not healthy:
                    logger.warning("Health check failed for model '%s', but process started", name)

            instance.state = ModelState.RUNNING
            instance.last_request_at = None

            event_bus = self._container.resolve(EventBus)
            await event_bus.publish(
                _ModelStarted(model_name=name, port=config.port)
            )

            logger.info("Model '%s' started on port %d", name, config.port)
            return instance

        except Exception:
            with self._lock:
                instance.state = ModelState.FAILED
            raise

    async def stop_model(self, name: str) -> ModelInstance:
        with self._lock:
            instance = self._instances.get(name)
            if instance is None:
                raise ValueError(f"Model '{name}' not found")
            if instance.state != ModelState.RUNNING:
                return instance
            instance.state = ModelState.STOPPING

        try:
            process_svc = self._container.resolve(ProcessManager)
            await process_svc.stop_process(name)

            instance.state = ModelState.STOPPED
            instance.pid = None
            instance.active_deployment = None

            event_bus = self._container.resolve(EventBus)
            await event_bus.publish(
                _ModelStopped(model_name=name, reason="manual")
            )

            logger.info("Model '%s' stopped", name)
            return instance

        except Exception:
            with self._lock:
                instance.state = ModelState.FAILED
            raise

    async def start_auto_start_models(self) -> list[str]:
        started = []
        for name, instance in self._instances.items():
            if instance.config.auto_start:
                try:
                    await self.start_model(name)
                    started.append(name)
                except Exception:
                    logger.exception("Failed to auto-start model '%s'", name)
        return started

    def get_model_by_port(self, port: int) -> ModelInstance | None:
        for instance in self._instances.values():
            if instance.config.port == port and instance.state == ModelState.RUNNING:
                return instance
        return None


class _ModelStarted:
    def __init__(self, model_name: str, port: int):
        self.model_name = model_name
        self.port = port
        self.timestamp = time.time()


class _ModelStopped:
    def __init__(self, model_name: str, reason: str):
        self.model_name = model_name
        self.reason = reason
        self.timestamp = time.time()
