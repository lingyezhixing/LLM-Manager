"""Typed operation results (replace (bool, str) tuples)."""

from __future__ import annotations

from dataclasses import dataclass

from llm_manager.domain.status import ModelStatus


@dataclass(frozen=True, slots=True)
class StartResult:
    ok: bool
    message: str
    status: ModelStatus


@dataclass(frozen=True, slots=True)
class StopResult:
    ok: bool
    message: str
    status: ModelStatus


@dataclass(frozen=True, slots=True)
class EnsureResult:
    ok: bool
    message: str
    status: ModelStatus


@dataclass(frozen=True, slots=True)
class ProbeResult:
    ok: bool
    message: str


@dataclass(frozen=True, slots=True)
class OperationResult:
    ok: bool
    message: str
