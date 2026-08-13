"""Config: 纯数据 + validate(DB 读取 → frozen dataclasses;设备名存储原样,匹配时归一化)。"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

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

    @classmethod
    def from_dict(cls, d: dict) -> Command:
        return cls(
            exe=d.get("exe", ""),
            args=tuple(d.get("args", [])),
            env=dict(d.get("env", {})),
            cwd=d.get("cwd"),
            conda_env=d.get("conda_env"),
        )

    def to_dict(self) -> dict:
        return {
            "exe": self.exe,
            "args": list(self.args),
            "env": self.env,
            "cwd": self.cwd,
            "conda_env": self.conda_env,
        }


@dataclass(frozen=True, slots=True)
class Scheme:
    config_source: str
    required_devices: frozenset[str]
    command: Command
    memory_mb: dict[str, int]

    @classmethod
    def from_dict(cls, d: dict) -> Scheme:
        return cls(
            config_source=d["config_source"],
            required_devices=frozenset(d.get("required_devices", [])),
            command=Command.from_dict(d.get("command", {})),
            memory_mb={k: int(v) for k, v in d.get("memory_mb", {}).items()},
        )


@dataclass(frozen=True, slots=True)
class PricingTier:
    tier_index: int
    min_input: int | None = 0  # None/negative treated as 0 (closed lower bound)
    max_input: int | None = None  # None/negative = unbounded (legacy -1)
    min_output: int | None = 0
    max_output: int | None = None
    input_price: float = 0.0
    output_price: float = 0.0
    cache_write_price: float = 0.0
    cache_read_price: float = 0.0

    @classmethod
    def from_dict(cls, d: dict) -> PricingTier:
        return cls(
            tier_index=d["tier_index"],
            min_input=d.get("min_input", 0),
            max_input=d.get("max_input"),
            min_output=d.get("min_output", 0),
            max_output=d.get("max_output"),
            input_price=d.get("input_price", 0.0),
            output_price=d.get("output_price", 0.0),
            cache_write_price=d.get("cache_write_price", 0.0),
            cache_read_price=d.get("cache_read_price", 0.0),
        )

    def to_dict(self) -> dict:
        return {
            "tier_index": self.tier_index,
            "min_input": self.min_input,
            "max_input": self.max_input,
            "min_output": self.min_output,
            "max_output": self.max_output,
            "input_price": self.input_price,
            "output_price": self.output_price,
            "cache_write_price": self.cache_write_price,
            "cache_read_price": self.cache_read_price,
        }


@dataclass(frozen=True, slots=True)
class Pricing:
    pricing_type: str = "tier"  # "tier" | "hourly"
    hourly_price: float = 0.0
    support_cache: bool = False  # 模型级:是否支持 prompt 缓存(缓存计费开关)
    tiers: tuple[PricingTier, ...] = ()

    @classmethod
    def from_dict(cls, d: dict) -> Pricing:
        return cls(
            pricing_type=d.get("pricing_type", "tier"),
            hourly_price=d.get("hourly_price", 0.0),
            support_cache=bool(d.get("support_cache", False)),
            tiers=tuple(PricingTier.from_dict(t) for t in d.get("tiers", [])),
        )

    def to_dict(self) -> dict:
        return {
            "pricing_type": self.pricing_type,
            "hourly_price": self.hourly_price,
            "support_cache": self.support_cache,
            "tiers": [t.to_dict() for t in self.tiers],
        }


@dataclass(frozen=True, slots=True)
class ModelConfig:
    aliases: tuple[
        str, ...
    ]  # 有序:aliases[0]=主别名=下游 served name(lmdeploy --model-name / llama.cpp -a)
    mode: str
    port: int
    auto_start: bool = False
    schemes: dict[str, Scheme] = field(default_factory=dict)
    pricing: Pricing = field(default_factory=Pricing)


# 启动命令变量占位符:{{port}} / {{alias}}(alias=aliases[0])。双大括号——
# 单大括号与 JSON 参数(如 --chat-template-kwargs {"enable_thinking":false})冲突,
# 替换会把 {e 误认成占位符;双大括号在 JSON 中几乎不出现,安全。
# 有占位符才替换,无则原样(写死值/不需要都不受影响)。
SUBST_PLACEHOLDERS = ("{{port}}", "{{alias}}")


def substitute_vars(text: str, model: ModelConfig) -> str:
    """启动命令变量替换:{{port}} → 模型端口,{{alias}} → 第一别名(下游 served name)。
    在 launch 构建 argv 时统一应用,使顶部端口/别名修改自动传导到启动命令。"""
    alias = model.aliases[0] if model.aliases else ""
    return text.replace(SUBST_PLACEHOLDERS[0], str(model.port)).replace(
        SUBST_PLACEHOLDERS[1], alias
    )


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


def validate(cfg: AppConfig) -> list[str]:
    errors: list[str] = []
    # 程序级:端口范围(DB 读取/写入的 int() 不查范围,在此兜底)
    if not 1 <= cfg.program.port <= 65535:
        errors.append(f"Program port {cfg.program.port} out of range (1-65535)")
    seen_ports: dict[int, str] = {}
    seen_aliases: dict[str, str] = {}
    valid_modes = {m.value for m in ModelMode}
    for name, m in cfg.models.items():
        if not name or not name.strip():  # 空模型名
            errors.append("Model name is empty/blank")
        if not 1 <= m.port <= 65535:  # 模型端口范围
            errors.append(f"Model '{name}' port {m.port} out of range (1-65535)")
        if m.port in seen_ports:
            errors.append(f"Port {m.port} shared by models '{seen_ports[m.port]}' and '{name}'")
        else:
            seen_ports[m.port] = name
        if not m.aliases:
            errors.append(f"Model '{name}' has no aliases")  # aliases[0]=下游 served name 必须
        for a in m.aliases:
            if not a or not a.strip():  # 空串别名
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
            errors.append(
                f"Model '{name}' mode '{m.mode}' not supported (supported: {sorted(valid_modes)})"
            )
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
            for pname, pval in (
                ("input_price", t.input_price),
                ("output_price", t.output_price),
                ("cache_write_price", t.cache_write_price),
                ("cache_read_price", t.cache_read_price),
            ):
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
    设备名存储原样(所见即所存),匹配时由 DeviceMonitor._tokens 归一化比对。供 DeviceMonitor 匹配。"""
    names: set[str] = set()
    for m in cfg.models.values():
        for scheme in m.schemes.values():
            names |= set(scheme.required_devices)
            names |= set(scheme.memory_mb)
    return names


def required_devices(model: ModelConfig) -> set[str]:
    """模型级 ∪ scheme.required_devices(无 adaptive scheme 时的错误消息共用)。"""
    return {d for s in model.schemes.values() for d in s.required_devices}


def resolve_alias(cfg: AppConfig, alias: str) -> str:
    for name, m in cfg.models.items():
        if alias == name or alias in m.aliases:
            return name
    raise KeyError(alias)


def auto_start_models(cfg: AppConfig) -> list[str]:
    """配置中 auto_start=True 的模型名列表(app 启动与托盘自动启动共用)。"""
    return [n for n, m in cfg.models.items() if m.auto_start]
