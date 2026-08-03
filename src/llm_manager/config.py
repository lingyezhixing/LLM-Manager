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
    # 程序级:端口范围(YAML 导入/config.load 的 int() 不查范围,在此兜底)
    if not 1 <= cfg.program.port <= 65535:
        errors.append(f"Program port {cfg.program.port} out of range (1-65535)")
    seen_ports: dict[int, str] = {}
    seen_aliases: dict[str, str] = {}
    valid_modes = {m.value for m in ModelMode}
    for name, m in cfg.models.items():
        if not name or not name.strip():                       # 空模型名
            errors.append("Model name is empty/blank")
        if not 1 <= m.port <= 65535:                           # 模型端口范围
            errors.append(f"Model '{name}' port {m.port} out of range (1-65535)")
        if m.port in seen_ports:
            errors.append(f"Port {m.port} shared by models '{seen_ports[m.port]}' and '{name}'")
        else:
            seen_ports[m.port] = name
        if not m.aliases:
            errors.append(f"Model '{name}' has no aliases")  # aliases[0]=下游 served name 必须
        for a in m.aliases:
            if not a or not a.strip():                         # 空串别名
                errors.append(f"Model '{name}' has empty alias")
                continue
            if a in seen_aliases:
                # 区分同模型内重复(误填)vs 跨模型共用(冲突)
                if seen_aliases[a] == name:
                    errors.append(f"Model '{name}' has duplicate alias '{a}'")
                else:
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


def scheme_memory_warnings(cfg: AppConfig) -> list[str]:
    """软告警(非 fatal):scheme 声明了 required_devices 但缺对应 memory_mb → 调度时该设备
    按 0 需求,显存检查被架空(check_and_free 收到空 required → 无 deficit → 直接放行)。

    作 WARNING 而非 error:部分合法配置刻意不填(如多设备备用方案/用户暂不启用驱逐),
    硬性报错会阻止已运行的配置启动。启动期 + 模型 CRUD 时日志告警,提示用户按需补全。
    详见 round3 审查 B4③。"""
    warnings: list[str] = []
    for name, m in cfg.models.items():
        for sname, scheme in m.schemes.items():
            missing = set(scheme.required_devices) - set(scheme.memory_mb)
            if missing:
                warnings.append(
                    f"Model '{name}' scheme '{sname}': required_devices {sorted(missing)} "
                    f"have no memory_mb entry → memory check bypassed for those devices")
    return warnings


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


def auto_start_models(cfg: AppConfig) -> list[str]:
    """配置中 auto_start=True 的模型名列表(app 启动与托盘自动启动共用)。"""
    return [n for n, m in cfg.models.items() if m.auto_start]
