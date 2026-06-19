from llm_manager.domain.model import Model, ModelKind, ModelMode


def test_model_mode_values_match_config_and_probe_keys():
    assert ModelMode.CHAT.value == "Chat"
    assert ModelMode.BASE.value == "Base"
    assert ModelMode.EMBEDDING.value == "Embedding"
    assert ModelMode.RERANKER.value == "Reranker"


def test_model_is_frozen_with_defaults():
    m = Model(
        primary_name="Qwen3.6-27B",
        aliases=frozenset({"Qwen3.6-27B", "qwen"}),
        mode=ModelMode.CHAT,
        port=10006,
    )
    assert m.auto_start is False
    assert m.kind is ModelKind.LOCAL
    try:
        m.port = 1  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("Model must be frozen")


def test_model_kind_local_value():
    assert ModelKind.LOCAL.value == "local"
