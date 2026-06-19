from llm_manager.domain.result import (
    EnsureResult,
    OperationResult,
    ProbeResult,
    StartResult,
    StopResult,
)
from llm_manager.domain.status import ModelStatus


def test_start_result_carries_status():
    r = StartResult(ok=True, message="up", status=ModelStatus.ROUTING)
    assert r.ok and r.status is ModelStatus.ROUTING


def test_probe_result_minimal():
    assert ProbeResult(ok=False, message="timeout").ok is False


def test_operation_result():
    assert OperationResult(ok=True, message="wol sent").message == "wol sent"


def test_ensure_and_stop_results_compile():
    EnsureResult(ok=True, message="routing", status=ModelStatus.ROUTING)
    StopResult(ok=True, message="stopped", status=ModelStatus.STOPPED)
