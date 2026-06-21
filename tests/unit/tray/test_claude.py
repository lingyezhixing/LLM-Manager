import json

from llm_manager.tray.claude import apply_preset


def test_apply_preset_updates_keys_preserves_others(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(json.dumps({"OTHER": "keep", "ANTHROPIC_BASE_URL": "old"}), encoding="utf-8")
    apply_preset(p, {"ANTHROPIC_BASE_URL": "http://new", "EXTRA": "x"})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["ANTHROPIC_BASE_URL"] == "http://new"
    assert data["OTHER"] == "keep"
    assert data["EXTRA"] == "x"


def test_apply_preset_creates_file_if_missing(tmp_path):
    p = tmp_path / "sub" / "settings.json"
    apply_preset(p, {"ANTHROPIC_BASE_URL": "http://x"})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["ANTHROPIC_BASE_URL"] == "http://x"
