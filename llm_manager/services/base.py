from __future__ import annotations

from abc import ABC

from llm_manager.container import Container


class BaseService(ABC):
    def __init__(self, container: Container):
        self._container = container

    async def on_start(self) -> None:
        pass

    async def on_stop(self) -> None:
        pass
