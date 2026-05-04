from __future__ import annotations

import logging
import threading
import time

from llm_manager.config.models import AppConfig, ModelConfigEntry
from llm_manager.database.repos.model_repo import ModelRuntimeRepository
from llm_manager.events import Event, EventBus
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
        self._event_bus: EventBus | None = None
        self._device_monitor: DeviceMonitor | None = None
        self._process_manager: ProcessManager | None = None
        self._plugin_registry: PluginRegistry | None = None
        self._runtime_repo: ModelRuntimeRepository | None = None

    async def on_start(self) -> None:
        self._app_config = self._container.resolve(AppConfig)
        self._event_bus = self._container.resolve(EventBus)
        self._device_monitor = self._container.resolve(DeviceMonitor)
        self._process_manager = self._container.resolve(ProcessManager)
        self._plugin_registry = self._container.resolve(PluginRegistry)
        self._runtime_repo = self._container.resolve(ModelRuntimeRepository)
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

        if deployment_name is not None:
            dep_name = deployment_name
            if dep_name not in config.deployments:
                raise ValueError(f"Deployment '{dep_name}' not found for model '{name}'")
            deployment = config.deployments[dep_name]
            if not self._device_monitor.check_devices_available(deployment.required_devices):
                raise RuntimeError(
                    f"Required devices not available: {deployment.required_devices}"
                )
        else:
            entry = self._app_config.models[name]
            online_devices = self._device_monitor.get_online_devices()
            result = entry.select_deployment(online_devices)
            if result is None:
                raise RuntimeError(
                    f"No compatible deployment for model '{name}' "
                    f"(online devices: {sorted(online_devices)})"
                )
            dep_name, _ = result
            deployment = config.deployments[dep_name]

        with self._lock:
            instance.state = ModelState.STARTING
            instance.active_deployment = dep_name

        try:
            process_info = await self._process_manager.start_process(
                name=name,
                script_path=str(deployment.script_path),
            )
            instance.pid = process_info.pid
            instance.started_at = time.time()

            try:
                instance.runtime_record_id = self._runtime_repo.record_start(
                    name, instance.started_at
                )
            except Exception:
                logger.exception("Failed to record model start for '%s'", name)
                instance.runtime_record_id = None

            with self._lock:
                instance.state = ModelState.HEALTH_CHECK

            interface_plugin = self._plugin_registry.get_interface(config.mode)
            if interface_plugin:
                healthy = await interface_plugin.health_check(
                    port=config.port,
                    model_name=name,
                    start_time=time.time(),
                    timeout_seconds=300.0,
                )
                if not healthy:
                    logger.error("Health check failed for model '%s', stopping process", name)
                    await self._process_manager.stop_process(name)
                    if instance.runtime_record_id is not None:
                        try:
                            self._runtime_repo.record_end_by_id(
                                instance.runtime_record_id, time.time()
                            )
                        except Exception:
                            pass
                        instance.runtime_record_id = None
                    with self._lock:
                        instance.state = ModelState.FAILED
                        instance.pid = None
                        instance.active_deployment = None
                        instance.started_at = None
                    raise RuntimeError(f"Health check failed for model '{name}'")

            with self._lock:
                instance.state = ModelState.RUNNING
            instance.last_request_at = None

            await self._event_bus.publish(
                _ModelStarted(model_name=name, port=config.port)
            )

            logger.info(
                "Model '%s' started on port %d (deployment: %s)",
                name, config.port, dep_name,
            )
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
            await self._process_manager.stop_process(name)

            if instance.runtime_record_id is not None:
                try:
                    self._runtime_repo.record_end_by_id(
                        instance.runtime_record_id, time.time()
                    )
                except Exception:
                    logger.exception("Failed to record model end for '%s'", name)
                instance.runtime_record_id = None

            instance.state = ModelState.STOPPED
            instance.pid = None
            instance.active_deployment = None
            instance.started_at = None
            instance.last_request_at = None

            await self._event_bus.publish(
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


class _ModelStarted(Event):
    def __init__(self, model_name: str, port: int):
        super().__init__()
        self.model_name = model_name
        self.port = port


class _ModelStopped(Event):
    def __init__(self, model_name: str, reason: str):
        super().__init__()
        self.model_name = model_name
        self.reason = reason
