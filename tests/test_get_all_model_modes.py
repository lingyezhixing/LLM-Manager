from core.config_manager import ConfigManager


def test_get_all_model_modes_returns_sorted_distinct_strings():
    cm = ConfigManager()
    modes = cm.get_all_model_modes()
    assert isinstance(modes, list)
    assert modes == sorted(set(modes)), "must be de-duplicated and sorted"
    assert all(isinstance(m, str) for m in modes)
    assert "Chat" in modes


def test_get_all_model_modes_is_subset_of_known_modes():
    cm = ConfigManager()
    modes = set(cm.get_all_model_modes())
    assert modes.issubset({"Chat", "Base", "Embedding", "Reranker"})
