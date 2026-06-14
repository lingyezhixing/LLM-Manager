from core.config_manager import ConfigManager

SUPPORTED = {"Chat", "Base", "Embedding", "Reranker"}


def test_real_config_has_no_mode_errors():
    cm = ConfigManager()
    errors = cm.validate_config()
    # No error string should mention an unsupported mode for the shipped config.
    mode_errors = [e for e in errors if "模式" in e or "mode" in e.lower()]
    assert mode_errors == [], f"unexpected mode errors: {mode_errors}"


def test_bad_mode_is_reported():
    cm = ConfigManager()
    # Inject one bogus mode into the in-memory config.
    first_key = next(iter(cm.config["Local-Models"]))
    original = cm.config["Local-Models"][first_key].get("mode")
    cm.config["Local-Models"][first_key]["mode"] = "Caht"
    try:
        errors = cm.validate_config()
        assert any("Caht" in e for e in errors), f"expected an error naming the bad mode, got: {errors}"
    finally:
        if original is not None:
            cm.config["Local-Models"][first_key]["mode"] = original
