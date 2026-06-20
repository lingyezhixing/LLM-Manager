"""DeviceRegistryImpl — STUB. Real hardware detection comes in the devices stub-fill phase."""

from __future__ import annotations

from llm_manager.domain.device import DeviceInfo, DeviceName


class DeviceRegistryImpl:
    """Stub: reports no devices. Satisfies ports.devices.DeviceRegistry."""

    def online_devices(self) -> frozenset[DeviceName]:
        return frozenset()

    def snapshot(self) -> dict[DeviceName, DeviceInfo]:
        return {}

    def refresh(self) -> None:
        # TODO(phase-devices): probe real hardware (NVIDIA/CPU/AMD).
        return None
