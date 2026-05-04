from __future__ import annotations

import logging

from llm_manager.schemas.device import DeviceStatus
from llm_manager.plugins.registry import PluginRegistry
from llm_manager.services.base import BaseService
from llm_manager.container import Container

logger = logging.getLogger(__name__)


class DeviceMonitor(BaseService):
    def __init__(self, container: Container):
        super().__init__(container)
        self._registry: PluginRegistry | None = None

    def _get_registry(self) -> PluginRegistry:
        if self._registry is None:
            self._registry = self._container.resolve(PluginRegistry)
        return self._registry

    def get_all_statuses(self) -> dict[str, DeviceStatus]:
        registry = self._get_registry()
        result = {}
        for plugin in registry.get_all_devices():
            try:
                result[plugin.name] = plugin.get_status()
            except Exception:
                logger.exception("Failed to get status for device '%s'", plugin.name)
        return result

    def get_status(self, device_name: str) -> DeviceStatus | None:
        plugin = self._get_registry().get_device(device_name)
        if plugin is None:
            return None
        try:
            return plugin.get_status()
        except Exception:
            logger.exception("Failed to get status for device '%s'", device_name)
            return None

    def check_devices_available(self, required_devices: list[str]) -> bool:
        statuses = self.get_all_statuses()
        for device_name in required_devices:
            status = statuses.get(device_name)
            if status is None:
                return False
        return True
