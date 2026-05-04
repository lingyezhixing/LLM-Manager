from __future__ import annotations

from abc import ABC, abstractmethod

from llm_manager.schemas.device import DeviceStatus


class DevicePlugin(ABC):
    name: str

    @abstractmethod
    def is_available(self) -> bool:
        ...

    @abstractmethod
    def get_status(self) -> DeviceStatus:
        ...
