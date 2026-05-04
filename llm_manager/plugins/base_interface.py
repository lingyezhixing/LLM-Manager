from __future__ import annotations

from abc import ABC, abstractmethod


class InterfacePlugin(ABC):
    name: str

    @abstractmethod
    def get_supported_endpoints(self) -> list[str]:
        ...

    @abstractmethod
    async def health_check(
        self,
        port: int,
        model_name: str,
        start_time: float,
        timeout_seconds: float = 300.0,
    ) -> bool:
        """双阶段健康检查。浅层检查服务可用性，深层检查接口功能。
        两阶段共享 start_time ~ timeout_seconds 的超时预算。"""
        ...

    @abstractmethod
    def validate_request(self, path: str, model_name: str) -> tuple[bool, str]:
        ...
