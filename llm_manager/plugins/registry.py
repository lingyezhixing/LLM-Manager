from __future__ import annotations

from llm_manager.plugins.base_device import DevicePlugin
from llm_manager.plugins.base_interface import InterfacePlugin


class PluginRegistry:
    def __init__(self):
        self._devices: dict[str, DevicePlugin] = {}
        self._interfaces: dict[str, InterfacePlugin] = {}

    def register_device(self, plugin: DevicePlugin) -> None:
        self._devices[plugin.name] = plugin

    def register_interface(self, plugin: InterfacePlugin) -> None:
        self._interfaces[plugin.name] = plugin

    def get_device(self, name: str) -> DevicePlugin | None:
        return self._devices.get(name)

    def get_interface(self, name: str) -> InterfacePlugin | None:
        return self._interfaces.get(name)

    def get_all_devices(self) -> list[DevicePlugin]:
        return list(self._devices.values())

    def get_all_interfaces(self) -> list[InterfacePlugin]:
        return list(self._interfaces.values())
