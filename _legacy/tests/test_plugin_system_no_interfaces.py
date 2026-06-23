import os

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BANNED = [
    "InterfacePluginLoader",
    "interface_loader",
    "interface_plugins",
    "get_interface_plugin",   # catches get_interface_plugin / get_all_interface_plugins
    "get_interface_plugin_dir",
    "interface_dir",
    "from plugins.interfaces",
    "plugins.interfaces.Base_Class",
]


def _scan(*subdirs):
    hits = []
    for sub in subdirs:
        base = os.path.join(REPO_ROOT, sub)
        for root, _, files in os.walk(base):
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(root, f)
                for i, line in enumerate(open(path, encoding="utf-8"), 1):
                    for needle in BANNED:
                        if needle in line:
                            hits.append(f"{path}:{i}: {line.strip()}")
    return hits


def test_no_interface_refs_in_core():
    assert not _scan("core"), "dangling interface refs in core/:\n" + "\n".join(_scan("core"))


def test_pluginmanager_init_has_no_interface_dir():
    import inspect
    from core.plugin_system import PluginManager
    params = list(inspect.signature(PluginManager.__init__).parameters.keys())
    assert "interface_dir" not in params, f"PluginManager.__init__ still takes interface_dir: {params}"
