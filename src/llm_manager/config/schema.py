"""Typed (pydantic v2) configuration schema — the YAML representation.

The loader (loader.py) converts these into domain objects. KNOWN_MODEL_SCALARS
is the SINGLE source of truth for which model-entry keys are scalars (everything
else at the model-entry level is a named device scheme) — replaces the old
3-site magic key-blacklist.
"""

from __future__ import annotations

import pathlib
from typing import Final

from pydantic import BaseModel, Field

KNOWN_MODEL_SCALARS: Final[frozenset[str]] = frozenset({"aliases", "mode", "port", "auto_start"})


class ProgramConfig(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    alive_time: int = 0
    log_level: str = "INFO"
    data_dir: pathlib.Path = pathlib.Path("./data")
    disable_gpu_monitoring: bool = False


class WakeOnLanConfig(BaseModel):
    broadcast_address: str
    mac_address: str


class ClaudePresets(BaseModel):
    presets: dict[str, dict[str, str]] = Field(default_factory=dict)
    settings_path: pathlib.Path | None = None


class SchemeConfig(BaseModel):
    required_devices: list[str]
    script_path: pathlib.Path
    memory_mb: dict[str, int]


class ModelConfigYAML(BaseModel):
    aliases: list[str]
    mode: str
    port: int
    auto_start: bool = False
    schemes: dict[str, SchemeConfig]


class AppConfig(BaseModel):
    program: ProgramConfig = Field(default_factory=ProgramConfig)
    models: dict[str, ModelConfigYAML] = Field(default_factory=dict)
    wake_on_lan: WakeOnLanConfig | None = None
    claude: ClaudePresets | None = None
