"""ModelController — STUB implementing ModelRuntimePort. Real lifecycle in the runtime phase."""

from __future__ import annotations

from llm_manager.config.schema import AppConfig
from llm_manager.domain.result import EnsureResult, StartResult, StopResult
from llm_manager.domain.status import ModelStatus
from llm_manager.ports.devices import DeviceRegistry
from llm_manager.ports.events import EventBus
from llm_manager.ports.metering import MeteringSink
from llm_manager.ports.process import ProcessBackend
from llm_manager.registry import Registry


class ModelController:
    """Stub. Satisfies ports.runtime.ModelRuntimePort. Construction signature is
    finalized here so Plan 3's container can wire it without further changes."""

    def __init__(
        self,
        *,
        config: AppConfig | None,
        process: ProcessBackend,
        devices: DeviceRegistry,
        probes: Registry,  # Registry[ModelMode, Probe]
        meter: MeteringSink,
        events: EventBus,
    ) -> None:
        self._config = config
        self._process = process
        self._devices = devices
        self._probes = probes
        self._meter = meter
        self._events = events

    def start(self, primary: str) -> StartResult:
        raise NotImplementedError("model lifecycle not implemented")  # TODO(phase-runtime)

    def stop(self, primary: str) -> StopResult:
        raise NotImplementedError("model lifecycle not implemented")  # TODO(phase-runtime)

    def status(self, primary: str) -> ModelStatus:
        return ModelStatus.STOPPED  # safe default until implemented

    async def ensure_running(self, primary: str) -> EnsureResult:
        raise NotImplementedError("model lifecycle not implemented")  # TODO(phase-runtime)

    def begin_request(self, primary: str) -> None:
        return None  # TODO(phase-runtime): pending-request tracking

    def end_request(self, primary: str) -> None:
        return None  # TODO(phase-runtime)
