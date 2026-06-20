"""A device-gated launch configuration for a model (one of several alternatives).

Replaces the flattened dict + magic key-blacklist in the old ConfigManager.
"""

from __future__ import annotations

import pathlib
from collections.abc import Mapping
from dataclasses import dataclass

from llm_manager.domain.device import DeviceName


@dataclass(frozen=True, slots=True)
class AdaptiveScheme:
    config_source: str
    required_devices: frozenset[DeviceName]
    script_path: pathlib.Path
    memory_mb: Mapping[DeviceName, int]
