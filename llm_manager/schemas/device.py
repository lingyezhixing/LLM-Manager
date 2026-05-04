from dataclasses import dataclass
from enum import Enum


class DeviceState(Enum):
    ONLINE = "online"
    OFFLINE = "offline"
    UNKNOWN = "unknown"


@dataclass
class DeviceStatus:
    name: str
    state: DeviceState = DeviceState.UNKNOWN
    memory_total_mb: int = 0
    memory_used_mb: int = 0
    memory_free_mb: int = 0
    temperature: float | None = None
    utilization: float | None = None
