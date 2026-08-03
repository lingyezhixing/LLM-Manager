"""Config: YAML load → frozen dataclasses. Device names normalized once (lowercase+strip)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml

PROGRAM_DEFAULTS: dict[str, str] = {
    "host": "0.0.0.0",
    "port": "8080",
    "alive_time": "60",
    "log_level": "INFO",
}

RETENTION_DEFAULTS: dict[str, str] = {
    "log_retention_days": "30",
    "log_retention_count": "10",
}


class ModelMode(str, Enum):
    """Probe selector; string values are config/registry keys."""
    CHAT = "Chat"
    EMBEDDING = "Embedding"
    RERANKER = "Reranker"


@dataclass(frozen=True, slots=True)
class Command:
    exe: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    conda_env: str | None = None


@dataclass(frozen=True, slots=True)
class Scheme:
    config_source: str
    required_devices: frozenset[str]
    command: Command
    memory_mb: dict[str, int]


@dataclass(frozen=True, slots=True)
class PricingTier:
    tier_index: int
    min_input: int | None = 0          # None/negative treated as 0 (closed lower bound)
    max_input: int | None = None       # None/negative = unbounded (legacy -1)
    min_output: int | None = 0
    max_output: int | None = None
    input_price: float = 0.0
    output_price: float = 0.0
    cache_write_price: float = 0.0
    cache_read_price: float = 0.0


@dataclass(frozen=True, slots=True)
class Pricing:
    pricing_type: str = "tier"         # "tier" | "hourly"
    hourly_price: float = 0.0
    support_cache: bool = False        # 模型级:是否支持 prompt 缓存(缓存计费开关)
    tiers: tuple[PricingTier, ...] = ()


@dataclass(frozen=True, slots=True)
class ModelConfig:
    primary_name: str
    aliases: tuple[str, ...]  # 有序:aliases[0]=主别名=下游 served name(lmdeploy --model-name / llama.cpp -a)
    mode: str
    port: int
    auto_start: bool = False
    schemes: dict[str, Scheme] = field(default_factory=dict)
    pricing: Pricing = field(default_factory=Pricing)


@dataclass(frozen=True, slots=True)
class ProgramConfig:
    host: str
    port: int
    alive_time: int
    log_level: str
    claude_settings_path: str | None = None
    log_retention_days: int = int(RETENTION_DEFAULTS["log_retention_days"])
    log_retention_count: int = int(RETENTION_DEFAULTS["log_retention_count"])


@dataclass(frozen=True, slots=True)
class WakeOnLanConfig:
    broadcast_address: str
    mac_address: str


@dataclass(frozen=True, slots=True)
class AppConfig:
    program: ProgramConfig
    models: dict[str, ModelConfig]
    wol: WakeOnLanConfig | None
    claude_configs: dict[str, dict[str, str]]


def _norm_device(name: str) -> str:
    return name.strip().lower()


def load(path: Path) -> AppConfig:
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    p = raw.get("program", {})
    program = ProgramConfig(
        host=p.get("host", PROGRAM_DEFAULTS["host"]),
        port=int(p.get("port", int(PROGRAM_DEFAULTS["port"]))),
        alive_time=int(p.get("alive_time", int(PROGRAM_DEFAULTS["alive_time"]))),
        log_level=p.get("log_level", PROGRAM_DEFAULTS["log_level"]),
        claude_settings_path=p.get("claude_settings_path"),
    )
    models: dict[str, ModelConfig] = {}
    reserved = {"aliases", "mode", "port", "auto_start"}
    for name, m in raw.get("Local-Models", {}).items():
        schemes: dict[str, Scheme] = {}
        for key, val in m.items():
            if key in reserved or not isinstance(val, dict):
                continue
            c = val["command"]
            schemes[key] = Scheme(
                config_source=key,
                required_devices=frozenset(_norm_device(d) for d in val.get("required_devices", [])),
                command=Command(
                    exe=c["exe"], args=tuple(c.get("args", [])), env=dict(c.get("env", {})),
                    cwd=c.get("cwd"), conda_env=c.get("conda_env")),
                memory_mb={_norm_device(k): int(v) for k, v in val.get("memory_mb", {}).items()},
            )
        models[name] = ModelConfig(
            primary_name=name,
            aliases=tuple(m.get("aliases", [])),  # tuple 保 yaml 顺序,aliases[0]=served
            mode=m.get("mode", "Chat"),
            port=int(m["port"]),
            auto_start=bool(m.get("auto_start", False)),
            schemes=schemes,
        )
    wol_raw = raw.get("wake_on_lan")
    wol = WakeOnLanConfig(wol_raw["broadcast_address"], wol_raw["mac_address"]) if wol_raw else None
    return AppConfig(program=program, models=models, wol=wol, claude_configs=raw.get("claude_configs", {}))


def validate(cfg: AppConfig) -> list[str]:
    errors: list[str] = []
    seen_ports: dict[int, str] = {}
    seen_aliases: dict[str, str] = {}
    valid_modes = {m.value for m in ModelMode}
    for name, m in cfg.models.items():
        if m.port in seen_ports:
            errors.append(f"Port {m.port} shared by models '{seen_ports[m.port]}' and '{name}'")
        else:
            seen_ports[m.port] = name
        if not m.aliases:
            errors.append(f"Model '{name}' has no aliases")  # aliases[0]=下游 served name 必须
        for a in m.aliases:
            if a in seen_aliases:
                errors.append(f"Alias '{a}' shared by models '{seen_aliases[a]}' and '{name}'")
            else:
                seen_aliases[a] = name
        if m.mode not in valid_modes:
            errors.append(f"Model '{name}' mode '{m.mode}' not supported (supported: {sorted(valid_modes)})")
        if not m.schemes:
            errors.append(f"Model '{name}' has no device scheme")
        for sname, scheme in m.schemes.items():
            if not scheme.command.exe:
                errors.append(f"Model '{name}' scheme '{sname}' has empty command.exe")
        # 验证定价层级
        seen_tiers: set[int] = set()
        for t in m.pricing.tiers:
            if t.tier_index in seen_tiers:
                errors.append(f"Model '{name}' has duplicate tier_index {t.tier_index}")
            seen_tiers.add(t.tier_index)
        for t in m.pricing.tiers:
            for pname, pval in (("input_price", t.input_price), ("output_price", t.output_price),
                                ("cache_write_price", t.cache_write_price),
                                ("cache_read_price", t.cache_read_price)):
                if pval < 0:
                    errors.append(f"Model '{name}' has negative price {pname}")
        if m.pricing.hourly_price < 0:
            errors.append(f"Model '{name}' has negative price hourly_price")
    return errors


def select_adaptive(model: ModelConfig, online: set[str]) -> Scheme | None:
    for scheme in model.schemes.values():
        if scheme.required_devices <= online:
            return scheme
    return None


def referenced_devices(cfg: AppConfig) -> set[str]:
    """收集 config 引用过的全部设备名 = ∪ scheme.required_devices ∪ ∪ scheme.memory_mb.keys()。
    config load 时已 _norm_device 归一化(小写+strip);此处幂等再收集。供 DeviceMonitor 匹配。"""
    names: set[str] = set()
    for m in cfg.models.values():
        for scheme in m.schemes.values():
            names |= set(scheme.required_devices)
            names |= set(scheme.memory_mb)
    return names


def resolve_alias(cfg: AppConfig, alias: str) -> str:
    for name, m in cfg.models.items():
        if alias == name or alias in m.aliases:
            return name
    raise KeyError(alias)
