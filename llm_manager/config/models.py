from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from llm_manager.schemas.billing import BillingMode


class ProgramConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    alive_time: int = 60
    disable_gpu_monitoring: bool = Field(False, alias="Disable_GPU_monitoring")
    device_plugin_dir: Path = Path("extensions/devices")
    interface_plugin_dir: Path = Path("extensions/interfaces")
    log_level: str = "INFO"
    token_tracker: list[str] = Field(
        default_factory=lambda: ["Chat", "Base", "Embedding", "Reranker"],
        alias="TokenTracker",
    )

    model_config = {"populate_by_name": True}

    def should_track_tokens(self, mode: str) -> bool:
        return mode in self.token_tracker


class AppConfig(BaseModel):
    program: ProgramConfig = ProgramConfig()
    models: dict[str, ModelConfigEntry] = Field(default_factory=dict, alias="Local-Models")
    billing: dict[str, BillingConfigEntry] | None = None

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def validate_config(self) -> "AppConfig":
        errors: list[str] = []
        seen_aliases: dict[str, str] = {}
        for model_name, entry in self.models.items():
            if not entry.aliases:
                errors.append(f"模型 '{model_name}' 的 aliases 为空")
            if not entry.get_deployments():
                errors.append(f"模型 '{model_name}' 没有有效的部署配置")
            for alias in entry.aliases:
                if alias in seen_aliases:
                    errors.append(
                        f"别名 '{alias}' 在模型 '{seen_aliases[alias]}' 和 '{model_name}' 中重复"
                    )
                seen_aliases[alias] = model_name
        if errors:
            raise ValueError("配置校验失败: " + "; ".join(errors))
        return self


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

    def select_deployment(self, online_devices: set[str]) -> tuple[str, ModelDeploymentEntry] | None:
        for name, entry in self.get_deployments().items():
            if set(entry.required_devices).issubset(online_devices):
                return name, entry
        return None


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
