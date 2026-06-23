from core.config_manager import ConfigManager


def test_whitelist_methods_removed():
    assert not hasattr(ConfigManager, "get_token_tracker_modes"), \
        "get_token_tracker_modes must be deleted"
    assert not hasattr(ConfigManager, "should_track_tokens_for_mode"), \
        "should_track_tokens_for_mode must be deleted"


def test_no_dangling_whitelist_references_in_source():
    import os
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hits = []
    for sub in ("core",):
        base = os.path.join(repo, sub)
        for root, _, files in os.walk(base):
            for f in files:
                if not f.endswith(".py"):
                    continue
                path = os.path.join(root, f)
                for i, line in enumerate(open(path, encoding="utf-8"), 1):
                    for needle in ("get_token_tracker_modes", "should_track_tokens_for_mode"):
                        if needle in line:
                            hits.append(f"{path}:{i}: {line.strip()}")
    assert not hits, "dangling whitelist refs:\n" + "\n".join(hits)
