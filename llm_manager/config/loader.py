from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path

import yaml
from pydantic import ValidationError

from llm_manager.config.models import AppConfig


class ConfigLoadError(Exception):
    def __init__(self, message: str, details: str | None = None):
        super().__init__(message)
        self.details = details


class ConfigLoader(ABC):
    @abstractmethod
    def load(self, path: Path) -> AppConfig: ...


class YamlConfigLoader(ConfigLoader):
    def load(self, path: Path) -> AppConfig:
        if not path.exists():
            raise ConfigLoadError(f"Configuration file not found: {path}")

        try:
            raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as e:
            raise ConfigLoadError(f"Invalid YAML syntax: {path}", details=str(e))

        if not isinstance(raw, dict):
            raise ConfigLoadError(f"Configuration file must be a YAML mapping: {path}")

        try:
            return AppConfig.model_validate(raw)
        except ValidationError as e:
            raise ConfigLoadError(
                f"Configuration validation failed: {path}",
                details=str(e),
            )
