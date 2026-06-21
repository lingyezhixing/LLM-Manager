import asyncio
import pytest

from llm_manager.state import (
    ModelStatus, set_status, get_status, is_starting, is_runnable, is_failed,
    record_failure, pending_count, inc_pending, dec_pending,
    begin_request, end_request, claim_start, finish_start, _reset,
)


@pytest.fixture(autouse=True)
def _clean():
    _reset()
    yield
    _reset()


def test_status_enum_six_states():
    assert {s.value for s in ModelStatus} == {"stopped", "starting", "init_script", "health_check", "routing", "failed"}


def test_default_status_is_stopped():
    assert get_status("M") == ModelStatus.STOPPED


def test_happy_path_transitions():
    set_status("M", ModelStatus.STARTING)
    set_status("M", ModelStatus.INIT_SCRIPT)
    set_status("M", ModelStatus.HEALTH_CHECK)
    set_status("M", ModelStatus.ROUTING)
    assert is_runnable("M")


def test_failure_transition_sets_reason():
    set_status("M", ModelStatus.STARTING)
    set_status("M", ModelStatus.FAILED, reason="boom")
    assert is_failed("M")


def test_failed_to_start_retry_is_legal():
    set_status("M", ModelStatus.STARTING)
    set_status("M", ModelStatus.FAILED, reason="x")
    set_status("M", ModelStatus.STARTING)
    assert is_starting("M")


def test_illegal_transition_raises():
    set_status("M", ModelStatus.STARTING)
    set_status("M", ModelStatus.INIT_SCRIPT)
    with pytest.raises(ValueError):
        set_status("M", ModelStatus.ROUTING)   # INIT_SCRIPT→ROUTING illegal


def test_force_stop_from_any_state():
    for target in (ModelStatus.STARTING, ModelStatus.INIT_SCRIPT, ModelStatus.HEALTH_CHECK, ModelStatus.ROUTING, ModelStatus.FAILED):
        _reset()
        set_status("M", ModelStatus.STARTING)
        if target == ModelStatus.INIT_SCRIPT:
            set_status("M", ModelStatus.INIT_SCRIPT)
        elif target == ModelStatus.HEALTH_CHECK:
            set_status("M", ModelStatus.INIT_SCRIPT)
            set_status("M", ModelStatus.HEALTH_CHECK)
        elif target == ModelStatus.ROUTING:
            set_status("M", ModelStatus.INIT_SCRIPT)
            set_status("M", ModelStatus.HEALTH_CHECK)
            set_status("M", ModelStatus.ROUTING)
        elif target == ModelStatus.FAILED:
            set_status("M", ModelStatus.FAILED, reason="x")
        set_status("M", ModelStatus.STOPPED, force=True)
        assert get_status("M") == ModelStatus.STOPPED


def test_record_failure_helper():
    set_status("M", ModelStatus.STARTING)
    record_failure("M", "segfault")
    assert is_failed("M")


def test_pending_inc_dec_clamped():
    inc_pending("M")
    inc_pending("M")
    assert pending_count("M") == 2
    dec_pending("M")
    dec_pending("M")
    dec_pending("M")
    assert pending_count("M") == 0


def test_begin_end_request_touch_activity():
    from llm_manager.state import _state
    begin_request("M")
    assert pending_count("M") == 1
    t1 = _state["M"].last_access
    end_request("M")
    assert pending_count("M") == 0
    assert _state["M"].last_access >= t1


def test_claim_start_winner_runs_loser_awaits():
    async def main():
        fut_a, won_a = claim_start("M")
        fut_b, won_b = claim_start("M")
        assert won_a is True and won_b is False
        assert fut_a is fut_b
        assert not fut_a.done()
        finish_start("M", ModelStatus.ROUTING)
        assert fut_a.result() == ModelStatus.ROUTING
        assert fut_b.result() == ModelStatus.ROUTING
        from llm_manager.state import _inflight
        assert "M" not in _inflight
    asyncio.run(main())


def test_claim_start_after_finish_allows_new_start():
    async def main():
        fut, won = claim_start("M")
        assert won
        finish_start("M", ModelStatus.FAILED)
        fut2, won2 = claim_start("M")
        assert won2 is True
        assert fut2 is not fut
    asyncio.run(main())


def test_pid_accessors():
    from llm_manager import state
    state._reset()
    state.record_pid("m1", 1234)
    assert state.get_pid("m1") == 1234
    state.clear_pid("m1")
    assert state.get_pid("m1") is None
