"""Config: YAML load → frozen dataclasses. Device names normalized once (lowercase+strip)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml


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
class ModelConfig:
    primary_name: str
    aliases: tuple[str, ...]  # 有序:aliases[0]=主别名=下游 served name(lmdeploy --model-name / llama.cpp -a)
    mode: str
    port: int
    auto_start: bool = False
    schemes: dict[str, Scheme] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProgramConfig:
    host: str
    port: int
    alive_time: int
    log_level: str
    log_dir: str = "logs"
    db_path: str = "data/llm_manager.db"
    claude_settings_path: str | None = None


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
        host=p.get("host", "0.0.0.0"),
        port=int(p.get("port", 8080)),
        alive_time=int(p.get("alive_time", 60)),
        log_level=p.get("log_level", "INFO"),
        log_dir=p.get("log_dir", "logs"),
        db_path=p.get("db_path", "data/llm_manager.db"),
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
