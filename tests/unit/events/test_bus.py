from llm_manager.domain.records import LifecycleEvent, LifecycleKind
from llm_manager.events.bus import EventBusImpl


def test_subscribe_and_publish():
    bus = EventBusImpl()
    received: list[LifecycleEvent] = []
    sub = bus.subscribe(lambda e: received.append(e))
    ev = LifecycleEvent(LifecycleKind.MODEL_ROUTING, "qwen", 1.5)
    bus.publish(ev)
    assert received == [ev]
    sub.cancel()
    bus.publish(ev)
    assert received == [ev]  # no further delivery after cancel


def test_multiple_subscribers():
    bus = EventBusImpl()
    a: list[LifecycleEvent] = []
    b: list[LifecycleEvent] = []
    bus.subscribe(a.append)
    bus.subscribe(b.append)
    bus.publish(LifecycleEvent(LifecycleKind.MODEL_FAILED, "qwen", 2.0))
    assert len(a) == 1 and len(b) == 1
