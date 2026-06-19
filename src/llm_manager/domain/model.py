"""Model identity and mode enums (pure, zero-IO)."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class ModelMode(Enum):
    """Health-probe selector; string values are the config/probe_registry keys."""

    CHAT = "Chat"
    BASE = "Base"
    EMBEDDING = "Embedding"
    RERANKER = "Reranker"


class ModelKind(Enum):
    """Where a model is served. Only LOCAL exists today."""

    LOCAL = "local"


@dataclass(frozen=True, slots=True)
class Model:
    """A configured local model — the unit the runtime orchestrates."""

    primary_name: str
    aliases: frozenset[str]
    mode: ModelMode
    port: int
    auto_start: bool = False
    kind: ModelKind = ModelKind.LOCAL
