"""Config: YAML load → frozen dataclasses. Device names normalized once (lowercase+strip)."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import yaml


class ModelMode(str, Enum):
    """Probe selector; string values are config/registry keys."""
    CHAT = "Chat"
    BASE = "Base"
    EMBEDDING = "Embedding"
    RERANKER = "Reranker"


@dataclass(frozen=True, slots=True)
class Scheme:
    config_source: str
    required_devices: frozenset[str]
    script_path: Path
    memory_mb: dict[str, int]


@dataclass(frozen=True, slots=True)
class ModelConfig:
    primary_name: str
    aliases: frozenset[str]
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
    disable_gpu_monitoring: bool = False


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
        disable_gpu_monitoring=bool(p.get("Disable_GPU_monitoring", False)),
    )
    models: dict[str, ModelConfig] = {}
    reserved = {"aliases", "mode", "port", "auto_start"}
    for name, m in raw.get("Local-Models", {}).items():
        schemes: dict[str, Scheme] = {}
        for key, val in m.items():
            if key in reserved or not isinstance(val, dict):
                continue
            schemes[key] = Scheme(
                config_source=key,
                required_devices=frozenset(_norm_device(d) for d in val.get("required_devices", [])),
                script_path=Path(val["script_path"]),
                memory_mb={_norm_device(k): int(v) for k, v in val.get("memory_mb", {}).items()},
            )
        models[name] = ModelConfig(
            primary_name=name,
            aliases=frozenset(m.get("aliases", [])),
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
            errors.append(f"Model '{name}' has no aliases")
        for a in m.aliases:
            if a in seen_aliases:
                errors.append(f"Alias '{a}' shared by models '{seen_aliases[a]}' and '{name}'")
            else:
                seen_aliases[a] = name
        if m.mode not in valid_modes:
            errors.append(f"Model '{name}' mode '{m.mode}' not supported (supported: {sorted(valid_modes)})")
        if not m.schemes:
            errors.append(f"Model '{name}' has no device scheme")
    return errors


def select_adaptive(model: ModelConfig, online: set[str]) -> Scheme | None:
    for scheme in model.schemes.values():
        if scheme.required_devices <= online:
            return scheme
    return None


def resolve_alias(cfg: AppConfig, alias: str) -> str:
    for name, m in cfg.models.items():
        if alias == name or alias in m.aliases:
            return name
    raise KeyError(alias)
