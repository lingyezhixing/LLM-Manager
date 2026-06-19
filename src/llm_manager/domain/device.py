"""Hardware device identity + normalized status snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import NewType

DeviceName = NewType("DeviceName", str)
"""Identity of a device; also the element of the online-devices set used for scheduling."""


@dataclass(frozen=True, slots=True)
class DeviceInfo:
    """Normalized device status snapshot (8 fields; replaces the old raw dict)."""

    device_name: DeviceName
    device_type: str
    memory_type: str
    total_memory_mb: int
    available_memory_mb: int
    used_memory_mb: int
    usage_percentage: float
    temperature_celsius: float | None
