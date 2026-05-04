from __future__ import annotations

import importlib.util
import inspect
import logging
from pathlib import Path

from llm_manager.plugins.base_device import DevicePlugin
from llm_manager.plugins.base_interface import InterfacePlugin

logger = logging.getLogger(__name__)


class PluginValidationError(Exception):
    def __init__(self, plugin_name: str, errors: list[str]):
        self.plugin_name = plugin_name
        self.errors = errors
        super().__init__(f"Plugin '{plugin_name}' validation failed: {'; '.join(errors)}")


class PluginLoader:
    def discover(self, plugin_dir: Path, base_class: type) -> list[type]:
        if not plugin_dir.exists():
            logger.warning("Plugin directory not found: %s", plugin_dir)
            return []

        classes = []
        for file_path in sorted(plugin_dir.glob("*.py")):
            if file_path.name.startswith("_") or file_path.name.startswith("base_"):
                continue

            module = self._load_module(file_path)
            if module is None:
                continue

            for _, cls in inspect.getmembers(module, inspect.isclass):
                if issubclass(cls, base_class) and cls is not base_class:
                    classes.append(cls)

        return classes

    def load(self, plugin_class: type, **kwargs) -> object:
        try:
            return plugin_class(**kwargs)
        except TypeError as e:
            raise PluginValidationError(
                plugin_class.__name__,
                [f"Constructor error: {e}"],
            )

    def validate(self, plugin: object) -> list[str]:
        errors = []
        required_attrs = []

        if isinstance(plugin, DevicePlugin):
            required_attrs = ["name", "is_available", "get_status"]
        elif isinstance(plugin, InterfacePlugin):
            required_attrs = ["name", "get_supported_endpoints", "health_check", "extract_token_usage"]

        for attr in required_attrs:
            if not hasattr(plugin, attr):
                errors.append(f"Missing required attribute/method: {attr}")

        if hasattr(plugin, "name") and not plugin.name:
            errors.append("Plugin name must be a non-empty string")

        return errors

    def _load_module(self, file_path: Path):
        module_name = file_path.stem
        spec = importlib.util.spec_from_file_location(module_name, file_path)
        if spec is None or spec.loader is None:
            logger.warning("Failed to load module spec: %s", file_path)
            return None

        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
            return module
        except Exception:
            logger.exception("Failed to load plugin module: %s", file_path)
            return None
