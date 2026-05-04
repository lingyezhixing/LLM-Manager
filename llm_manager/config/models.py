from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from llm_manager.schemas.billing import BillingMode


class ProgramConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    alive_time: int = 60
    disable_gpu_monitoring: bool = Field(False, alias="Disable_GPU_monitoring")
    device_plugin_dir: Path = Path("extensions/devices")
    interface_plugin_dir: Path = Path("extensions/interfaces")
    log_level: str = "INFO"
    token_tracker: list[str] = Field(default_factory=lambda: ["Chat", "Base", "Embedding", "Reranker"])

    model_config = {"populate_by_name": True}


class AppConfig(BaseModel):
    program: ProgramConfig = ProgramConfig()
    models: dict[str, ModelConfigEntry] = Field(default_factory=dict, alias="Local-Models")
    billing: dict[str, BillingConfigEntry] | None = None

    model_config = {"populate_by_name": True}


class ModelDeploymentEntry(BaseModel):
    required_devices: list[str]
    script_path: Path
    memory_mb: dict[str, int]


class ModelConfigEntry(BaseModel):
    aliases: list[str]
    mode: str
    port: int
    auto_start: bool = False

    model_config = {"extra": "allow"}

    def get_deployments(self) -> dict[str, ModelDeploymentEntry]:
        deployments = {}
        for field_name, field_value in self:
            if field_name in ("aliases", "mode", "port", "auto_start"):
                continue
            if isinstance(field_value, dict):
                try:
                    deployments[field_name] = ModelDeploymentEntry(**field_value)
                except Exception:
                    continue
        return deployments


class BillingTierEntry(BaseModel):
    name: str
    start: int
    end: int
    price: float


class BillingConfigEntry(BaseModel):
    model_name: str
    mode: BillingMode = BillingMode.TIERED
    tiers: list[BillingTierEntry] | None = None
    hourly_rate: float | None = None
