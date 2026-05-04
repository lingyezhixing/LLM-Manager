from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path


class ModelState(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    HEALTH_CHECK = "health_check"
    RUNNING = "running"
    STOPPING = "stopping"
    FAILED = "failed"


@dataclass
class DeploymentConfig:
    required_devices: list[str]
    script_path: Path
    memory_mb: dict[str, int]


@dataclass
class ModelConfig:
    name: str
    aliases: list[str]
    mode: str
    port: int
    auto_start: bool = False
    deployments: dict[str, DeploymentConfig] = field(default_factory=dict)


@dataclass
class ModelInstance:
    name: str
    config: ModelConfig
    state: ModelState = ModelState.STOPPED
    active_deployment: str | None = None
    pid: int | None = None
    started_at: float | None = None
    last_request_at: float | None = None
    runtime_record_id: int | None = None
