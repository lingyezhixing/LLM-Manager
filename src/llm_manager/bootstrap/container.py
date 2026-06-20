"""AppContainer — the single construction point. Eagerly builds every component
in dependency order and injects via constructors (no singletons, no set_X back-refs).
Created once per process in create_app(); the lifespan only starts/stops services."""

from __future__ import annotations

import pathlib

# Importing these modules populates the global registries (token_parsers,
# endpoint_shapes, probes) as a side effect — do it once at container import time.
import llm_manager.devices.probes  # noqa: F401
import llm_manager.metering.parsers  # noqa: F401
from llm_manager.bootstrap.services import ManagedServices
from llm_manager.config.loader import load
from llm_manager.config.schema import AppConfig
from llm_manager.config.validator import validate
from llm_manager.devices.refresh_service import DeviceRefreshService
from llm_manager.devices.registry import DeviceRegistryImpl
from llm_manager.events.bus import EventBusImpl
from llm_manager.gateway.app import create_app
from llm_manager.gateway.proxy import GatewayImpl
from llm_manager.ops.service import SystemOpsService
from llm_manager.persistence.repository import Repository
from llm_manager.persistence.store import SqliteStore
from llm_manager.ports.devices import probes
from llm_manager.process.backend import SubprocessBackend
from llm_manager.process.reaper_service import ReaperService
from llm_manager.runtime.controller import ModelController
from llm_manager.runtime.idle_service import IdleCheckService


class AppContainer:
    def __init__(self, config_path: pathlib.Path) -> None:
        self.config: AppConfig = load(config_path)

        errors = validate(self.config)
        if errors:
            raise ValueError("Invalid config: " + "; ".join(errors))

        self.events = EventBusImpl()
        self.store = SqliteStore(self.config.program.data_dir / "monitoring.db")
        self.store.connect()
        self.meter = Repository(self.store)
        self.devices = DeviceRegistryImpl()
        self.process = SubprocessBackend()
        self.runtime = ModelController(
            config=self.config,
            process=self.process,
            devices=self.devices,
            probes=probes,
            meter=self.meter,
            events=self.events,
        )
        self.ops = SystemOpsService(self.config, self.runtime, self.devices)
        self.services = ManagedServices(
            [
                IdleCheckService(self.runtime),
                DeviceRefreshService(self.devices),
                ReaperService(self.process),
            ]
        )
        self.gateway = GatewayImpl(runtime=self.runtime, meter=self.meter, events=self.events)
        self.app = create_app(self)

    def shutdown(self) -> None:
        self.services.stop()
        self.store.close()
