"""Device registry + probe contracts; owns the `probes` registry instance
(single source of truth for supported model modes — read by config validator)."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from llm_manager.domain.device import DeviceInfo, DeviceName
from llm_manager.domain.model import ModelMode
from llm_manager.domain.result import ProbeResult
from llm_manager.registry import Registry


@runtime_checkable
class Probe(Protocol):
    def __call__(
        self, alias: str, port: int, start_time: float | None = None, timeout: float = 300
    ) -> ProbeResult: ...


@runtime_checkable
class DevicePlugin(Protocol):
    device_name: DeviceName

    def is_online(self) -> bool: ...

    def get_devices_info(self) -> DeviceInfo: ...


@runtime_checkable
class DeviceRegistry(Protocol):
    def online_devices(self) -> frozenset[DeviceName]: ...

    def snapshot(self) -> dict[DeviceName, DeviceInfo]: ...

    def refresh(self) -> None: ...


# The single source of truth for supported modes. Populated by @probe(...) in
# devices/probes.py (Plan 2). config.validator cross-checks modes against ModelMode.
probes: Registry[ModelMode, Probe] = Registry()

# Concrete device plugins (spec §8). Populated by @device in devices/* (Plan 2).
devices: Registry[DeviceName, DevicePlugin] = Registry()
