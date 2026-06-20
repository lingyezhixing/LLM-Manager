import pytest

from llm_manager.domain.status import (
    IllegalTransitionError,
    ModelStatus,
    TransitionTable,
)


def test_stop_to_start_allowed():
    assert (
        TransitionTable.transition(ModelStatus.STOPPED, ModelStatus.STARTING)
        == ModelStatus.STARTING
    )


def test_routing_to_stopped_allowed():
    assert (
        TransitionTable.transition(ModelStatus.ROUTING, ModelStatus.STOPPED)
        == ModelStatus.STOPPED
    )


def test_illegal_transition_raises():
    with pytest.raises(IllegalTransitionError):
        TransitionTable.transition(ModelStatus.STOPPED, ModelStatus.ROUTING)


def test_can_transition_predicate():
    assert TransitionTable.can_transition(ModelStatus.STARTING, ModelStatus.INIT_SCRIPT)
    assert not TransitionTable.can_transition(ModelStatus.FAILED, ModelStatus.ROUTING)


def test_self_transition_not_allowed_unless_explicit():
    with pytest.raises(IllegalTransitionError):
        TransitionTable.transition(ModelStatus.ROUTING, ModelStatus.ROUTING)
