import inspect
import core.probes as probes


def test_probe_registry_has_four_modes():
    assert set(probes.probe_registry.keys()) == {"Chat", "Base", "Embedding", "Reranker"}


def test_probe_signatures_are_pinned():
    """Contract from spec §4.1: positional (model_alias, port, start_time, timeout)."""
    expected = ["model_alias", "port", "start_time", "timeout"]
    for mode, fn in probes.probe_registry.items():
        params = list(inspect.signature(fn).parameters.keys())
        assert params == expected, f"{mode} probe signature mismatch: {params}"


def test_probe_on_unreachable_port_returns_false():
    """A refused port must NOT return True. (~2s; guards against a signature/order
    swap that would make the probe no-op and flip status to ROUTING prematurely.)"""
    ok, msg = probes.probe_chat("nonexistent-model", port=1, start_time=None, timeout=1)
    assert ok is False
    assert isinstance(msg, str) and msg
