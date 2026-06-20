from llm_manager.bootstrap.services import ManagedServices


class _FakeService:
    def __init__(self):
        self.started = False
        self.stopped = False
    def start(self): self.started = True
    def stop(self): self.stopped = True


def test_start_stop_all():
    a, b = _FakeService(), _FakeService()
    mgr = ManagedServices([a, b])
    mgr.start()
    assert a.started and b.started
    mgr.stop()
    assert a.stopped and b.stopped


def test_stop_continues_after_a_failure():
    class _Bad:
        def start(self): pass
        def stop(self): raise RuntimeError("boom")
    a = _FakeService()
    mgr = ManagedServices([_Bad(), a])
    mgr.start()
    mgr.stop()  # must not raise; a still stopped
    assert a.stopped
