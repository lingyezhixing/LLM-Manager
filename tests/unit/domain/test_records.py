from llm_manager.domain.meter import TokenUsage
from llm_manager.domain.records import LifecycleEvent, LifecycleKind, RequestRecord, Session


def test_request_record_carries_usage():
    r = RequestRecord(
        model_name="qwen", start_time=1.0, end_time=2.0,
        usage=TokenUsage(10, 5, 0, 10),
    )
    assert r.usage.output_tokens == 5


def test_session_fields():
    s = Session(entity="qwen", start_time=1.0, end_time=2.0)
    assert s.entity == "qwen"


def test_lifecycle_event_kind_values():
    ev = LifecycleEvent(LifecycleKind.MODEL_ROUTING, "qwen", 1.5)
    assert ev.kind is LifecycleKind.MODEL_ROUTING
    assert LifecycleEvent(LifecycleKind.MODEL_EVICTED, "qwen", 1.5).primary_name == "qwen"
