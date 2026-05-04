from __future__ import annotations

from abc import ABC, abstractmethod

from llm_manager.schemas.request import TokenUsage


class InterfacePlugin(ABC):
    name: str

    @abstractmethod
    def get_supported_endpoints(self) -> list[str]:
        ...

    @abstractmethod
    async def health_check(self, port: int, timeout: float = 300.0) -> bool:
        ...

    @abstractmethod
    def extract_token_usage(self, response: dict) -> TokenUsage:
        ...
