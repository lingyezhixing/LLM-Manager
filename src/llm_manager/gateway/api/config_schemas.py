"""config 写路径的请求模型 + Pydantic→ModelConfig 转换(自 config_api 拆出,2026-08-14)。"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from llm_manager.config import ModelConfig, Pricing, Scheme


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
    DeviceMonitor._tokens 归一化比对(与 YAML 导入一致)。重复 config_source → ValueError(→ 422)。"""
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
