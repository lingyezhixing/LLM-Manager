"""config 写路径的请求模型 + Pydantic→ModelConfig 转换。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from llm_manager.config import (
    CloudMapping,
    CloudModel,
    CloudProvider,
    ModelConfig,
    Pricing,
    PricingTier,
    Scheme,
    TimeWindow,
)


class ProgramUpdate(BaseModel):
    host: str | None = None
    port: int | None = Field(default=None, ge=1, le=65535)
    alive_time: int | None = Field(default=None, ge=0)
    log_level: str | None = None
    claude_settings_path: str | None = None


class LogRetentionUpdate(BaseModel):
    """日志保留规则:恒生效的两个参数(按时间保留 N 天 + 按条数保留 N 条,系统与模型日志同时适用)。"""

    days: int | None = Field(default=None, ge=1)
    count: int | None = Field(default=None, ge=1)


class CommandInput(BaseModel):
    exe: str
    args: list[str] = []
    env: dict[str, str] = {}
    cwd: str | None = None
    conda_env: str | None = None


class SchemeInput(BaseModel):
    config_source: str
    required_devices: list[str] = []
    command: CommandInput
    memory_mb: dict[str, int] = {}


class PricingTierInput(BaseModel):
    tier_index: int
    min_input: int | None = 0
    max_input: int | None = None
    min_output: int | None = 0
    max_output: int | None = None
    input_price: float = 0.0
    output_price: float = 0.0
    cache_write_price: float = 0.0
    cache_read_price: float = 0.0


class PricingInput(BaseModel):
    pricing_type: Literal["tier", "hourly"] = "tier"
    hourly_price: float = 0.0
    support_cache: bool = False
    tiers: list[PricingTierInput] = []


class ModelDefInput(BaseModel):
    name: str
    mode: str  # config.validate 校验 Chat/Embedding/Reranker
    port: int = Field(ge=1, le=65535)
    auto_start: bool = False
    aliases: list[str]  # 非空(validate)
    schemes: list[SchemeInput]  # 非空(validate)
    pricing: PricingInput = Field(default_factory=PricingInput)


def _to_model_config(body: ModelDefInput) -> ModelConfig:
    """Pydantic 输入 → frozen ModelConfig。设备名存储原样(所见即所存),匹配时由
    DeviceMonitor._tokens 归一化比对。重复 config_source → ValueError(→ 422)。"""
    schemes: dict[str, Scheme] = {}
    for s in body.schemes:
        if s.config_source in schemes:
            raise ValueError(f"duplicate scheme config_source '{s.config_source}'")
        schemes[s.config_source] = Scheme.from_dict(s.model_dump())
    return ModelConfig(
        aliases=tuple(body.aliases),
        mode=body.mode,
        port=body.port,
        auto_start=body.auto_start,
        schemes=schemes,
        pricing=Pricing.from_dict(body.pricing.model_dump()),
    )


class CloudTierInput(BaseModel):
    tier_index: int
    min_input: int | None = 0
    max_input: int | None = None
    min_output: int | None = 0
    max_output: int | None = None
    input_price: float = 0.0
    output_price: float = 0.0
    cache_write_price: float = 0.0
    cache_read_price: float = 0.0


class CloudTimeWindowInput(BaseModel):
    start_min: int
    end_min: int


class CloudModelInput(BaseModel):
    model_name: str
    support_cache: bool = False
    dual_pricing: bool = False
    offpeak_windows: list[CloudTimeWindowInput] = []
    tiers_base: list[CloudTierInput] = []
    tiers_offpeak: list[CloudTierInput] = []


class CloudMappingInput(BaseModel):
    local_path: str
    target_url: str
    auth_style: Literal["bearer", "x-api-key", "none"] = "bearer"


class ProviderInput(BaseModel):
    name: str
    api_key: str = ""
    enabled: bool = True
    openai_base: str = ""
    responses_base: str = ""
    claude_base: str = ""
    extra_headers: dict[str, str] = {}
    models: list[CloudModelInput] = []
    mappings: list[CloudMappingInput] = []


def _to_cloud_provider(body: ProviderInput) -> CloudProvider:
    return CloudProvider(
        name=body.name,
        api_key=body.api_key,
        enabled=body.enabled,
        openai_base=body.openai_base,
        responses_base=body.responses_base,
        claude_base=body.claude_base,
        extra_headers=tuple(body.extra_headers.items()),
        models=tuple(
            CloudModel(
                model_name=m.model_name,
                support_cache=m.support_cache,
                dual_pricing=m.dual_pricing,
                offpeak_windows=tuple(
                    TimeWindow(w.start_min, w.end_min) for w in m.offpeak_windows
                ),
                tiers_base=tuple(PricingTier.from_dict(t.model_dump()) for t in m.tiers_base),
                tiers_offpeak=tuple(PricingTier.from_dict(t.model_dump()) for t in m.tiers_offpeak),
            )
            for m in body.models
        ),
        mappings=tuple(
            CloudMapping(x.local_path, x.target_url, x.auth_style) for x in body.mappings
        ),
    )


def cloud_provider_to_dict(p: CloudProvider) -> dict:
    return {
        "name": p.name,
        "api_key": p.api_key,
        "enabled": p.enabled,
        "openai_base": p.openai_base,
        "responses_base": p.responses_base,
        "claude_base": p.claude_base,
        "extra_headers": dict(p.extra_headers),
        "models": [
            {
                "model_name": m.model_name,
                "support_cache": m.support_cache,
                "dual_pricing": m.dual_pricing,
                "offpeak_windows": [
                    {"start_min": w.start_min, "end_min": w.end_min} for w in m.offpeak_windows
                ],
                "tiers_base": [t.to_dict() for t in m.tiers_base],
                "tiers_offpeak": [t.to_dict() for t in m.tiers_offpeak],
            }
            for m in p.models
        ],
        "mappings": [
            {"local_path": x.local_path, "target_url": x.target_url, "auth_style": x.auth_style}
            for x in p.mappings
        ],
    }
