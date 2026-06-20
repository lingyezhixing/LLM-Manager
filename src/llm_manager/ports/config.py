"""Config access port."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class ConfigPort(Protocol):
    def program(self) -> object:
        """Return the ProgramConfig (typed in config.schema; typed loosely here
        to avoid a ports->config import cycle)."""
        ...

    def catalog(self) -> object:
        """Return the ModelCatalog."""
        ...
