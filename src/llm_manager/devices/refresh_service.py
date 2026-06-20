"""DeviceRefreshService — STUB Service (no-op loop)."""

from __future__ import annotations

from llm_manager.ports.devices import DeviceRegistry


class DeviceRefreshService:
    def __init__(self, devices: DeviceRegistry) -> None:
        self._devices = devices

    def start(self) -> None:
        # TODO(phase-devices): periodic devices.refresh() loop.
        return None

    def stop(self) -> None:
        return None
