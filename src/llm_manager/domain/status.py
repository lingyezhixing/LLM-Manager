"""Centralized model lifecycle state machine — the single source of truth
for valid ModelStatus values and the transitions between them."""

from __future__ import annotations

from enum import Enum


class ModelStatus(Enum):
    STOPPED = "stopped"
    STARTING = "starting"
    INIT_SCRIPT = "init_script"
    HEALTH_CHECK = "health_check"
    ROUTING = "routing"
    FAILED = "failed"


class IllegalTransitionError(RuntimeError):
    """Raised when a state transition is not in the TransitionTable."""


class TransitionTable:
    """The only place that defines which (from -> to) moves are legal."""

    _ALLOWED: frozenset[tuple[ModelStatus, ModelStatus]] = frozenset(
        {
            (ModelStatus.STOPPED, ModelStatus.STARTING),
            (ModelStatus.STARTING, ModelStatus.INIT_SCRIPT),
            (ModelStatus.STARTING, ModelStatus.STOPPED),
            (ModelStatus.STARTING, ModelStatus.FAILED),
            (ModelStatus.INIT_SCRIPT, ModelStatus.HEALTH_CHECK),
            (ModelStatus.INIT_SCRIPT, ModelStatus.STOPPED),
            (ModelStatus.INIT_SCRIPT, ModelStatus.FAILED),
            (ModelStatus.HEALTH_CHECK, ModelStatus.ROUTING),
            (ModelStatus.HEALTH_CHECK, ModelStatus.STOPPED),
            (ModelStatus.HEALTH_CHECK, ModelStatus.FAILED),
            (ModelStatus.ROUTING, ModelStatus.STOPPED),
            (ModelStatus.ROUTING, ModelStatus.FAILED),
            (ModelStatus.FAILED, ModelStatus.STOPPED),
        }
    )

    @classmethod
    def can_transition(cls, from_status: ModelStatus, to_status: ModelStatus) -> bool:
        return (from_status, to_status) in cls._ALLOWED

    @classmethod
    def transition(cls, from_status: ModelStatus, to_status: ModelStatus) -> ModelStatus:
        if not cls.can_transition(from_status, to_status):
            raise IllegalTransitionError(
                f"Illegal model state transition: {from_status.value} -> {to_status.value}"
            )
        return to_status
