"""Load config.yaml -> typed AppConfig, and convert to domain Model objects.

Single construction point — replaces the old Monitor building its own
ConfigManager. Builds O(1) alias->primary / primary->entry indexes.
"""

from __future__ import annotations

import pathlib

import yaml

from llm_manager.config.schema import (
    KNOWN_MODEL_SCALARS,
    AppConfig,
    ClaudePresets,
    ModelConfigYAML,
    ProgramConfig,
    SchemeConfig,
    WakeOnLanConfig,
)
from llm_manager.domain.model import Model, ModelMode

_LOCAL_MODELS_KEY = "Local-Models"


def load(path: str | pathlib.Path) -> AppConfig:
    """Read YAML and return a validated AppConfig.

    The on-disk model-entry shape mixes scalar fields (aliases/mode/port/auto_start)
    with named device schemes at the same mapping level. We split them here using
    KNOWN_MODEL_SCALARS as the single discriminator — no other site hardcodes that list.
    """
    text = pathlib.Path(path).read_text(encoding="utf-8")
    raw = yaml.safe_load(text) or {}

    program = ProgramConfig.model_validate(raw.get("program", {}))

    models: dict[str, ModelConfigYAML] = {}
    for name, entry in (raw.get(_LOCAL_MODELS_KEY) or {}).items():
        if not isinstance(entry, dict):
            raise ValueError(f"Model entry '{name}' must be a mapping")
        scalars = {k: entry[k] for k in KNOWN_MODEL_SCALARS if k in entry}
        schemes = {
            k: SchemeConfig.model_validate(v)
            for k, v in entry.items()
            if k not in KNOWN_MODEL_SCALARS
        }
        scalars["schemes"] = schemes
        models[name] = ModelConfigYAML.model_validate(scalars)

    wake = raw.get("wake_on_lan")
    claude = raw.get("claude_configs") or raw.get("claude")

    return AppConfig(
        program=program,
        models=models,
        wake_on_lan=WakeOnLanConfig.model_validate(wake) if wake else None,
        claude=_parse_claude(claude, raw.get("program", {})),
    )


def _parse_claude(claude_raw: object, program_raw: dict) -> ClaudePresets | None:
    if not claude_raw:
        return None
    settings_path = program_raw.get("claude_settings_path")
    return ClaudePresets.model_validate(
        {
            "presets": claude_raw,
            "settings_path": settings_path,
        }
    )


def catalog_domain_models(cfg: AppConfig) -> list[Model]:
    """Convert AppConfig model entries into lightweight domain Model objects."""
    out: list[Model] = []
    for entry in cfg.models.values():
        out.append(
            Model(
                primary_name=entry.aliases[0],
                aliases=frozenset(entry.aliases),
                mode=ModelMode(entry.mode),
                port=entry.port,
                auto_start=entry.auto_start,
            )
        )
    return out


def alias_to_primary(cfg: AppConfig) -> dict[str, str]:
    """O(1) alias -> primary name index, with collision detection."""
    index: dict[str, str] = {}
    for entry in cfg.models.values():
        primary = entry.aliases[0]
        for alias in entry.aliases:
            if alias in index:
                raise ValueError(f"Duplicate alias across models: {alias!r}")
            index[alias] = primary
    return index


__all__ = ["load", "catalog_domain_models", "alias_to_primary"]
