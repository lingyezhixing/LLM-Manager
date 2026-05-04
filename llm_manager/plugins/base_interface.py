from __future__ import annotations

from abc import ABC, abstractmethod


class InterfacePlugin(ABC):
    name: str

    @abstractmethod
    def get_supported_endpoints(self) -> list[str]:
        ...

    @abstractmethod
    async def health_check(self, port: int, timeout: float = 300.0) -> bool:
        ...

    @abstractmethod
    def validate_request(self, path: str, model_name: str) -> tuple[bool, str]:
        ...
