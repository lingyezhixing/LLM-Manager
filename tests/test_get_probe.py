from core.plugin_system import PluginManager
from core.probes import probe_registry


def make_pm():
    return PluginManager("plugins/devices")


def test_get_probe_returns_registered_fn_for_each_mode():
    pm = make_pm()
    for mode, fn in probe_registry.items():
        assert pm.get_probe(mode) is fn


def test_get_probe_unknown_mode_returns_none():
    pm = make_pm()
    assert pm.get_probe("Bogus") is None
