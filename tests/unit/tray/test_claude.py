import json

from llm_manager.tray.claude import apply_preset, detect_current_preset


def test_apply_preset_nests_under_env(tmp_path):
    # Claude Code settings.json 把 env 变量嵌在 data["env"] 下,非顶层
    p = tmp_path / "settings.json"
    apply_preset(p, {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8080", "EXTRA": "x"})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8080"
    assert data["env"]["EXTRA"] == "x"
    assert "ANTHROPIC_BASE_URL" not in data  # 不该出现在顶层


def test_apply_preset_preserves_existing_top_and_env_keys(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(
        json.dumps(
            {
                "OTHER": "keep",  # 顶层其他键保留
                "env": {"ANTHROPIC_BASE_URL": "old", "KEEP_ENV": "v"},
            }
        ),
        encoding="utf-8",
    )
    apply_preset(p, {"ANTHROPIC_BASE_URL": "http://new"})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["OTHER"] == "keep"
    assert data["env"]["ANTHROPIC_BASE_URL"] == "http://new"
    assert data["env"]["KEEP_ENV"] == "v"  # 已有 env 键保留


def test_apply_preset_creates_file_with_parent_dir(tmp_path):
    p = tmp_path / "sub" / "settings.json"
    apply_preset(p, {"ANTHROPIC_BASE_URL": "http://x"})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["env"]["ANTHROPIC_BASE_URL"] == "http://x"


def test_apply_preset_rebuilds_on_corrupt_json(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("{ not valid json", encoding="utf-8")
    apply_preset(p, {"ANTHROPIC_BASE_URL": "http://x"})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["env"]["ANTHROPIC_BASE_URL"] == "http://x"


_PRESETS = {
    "GLM": {"ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic"},
    "Local": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8080"},
}


def test_detect_current_preset_matches(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "http://127.0.0.1:8080"}}), encoding="utf-8"
    )
    assert detect_current_preset(p, _PRESETS) == "Local"
    p.write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://open.bigmodel.cn/api/anthropic"}}),
        encoding="utf-8",
    )
    assert detect_current_preset(p, _PRESETS) == "GLM"


def test_detect_current_preset_unknown_fallback(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text(
        json.dumps({"env": {"ANTHROPIC_BASE_URL": "https://other.example.com"}}), encoding="utf-8"
    )
    assert detect_current_preset(p, _PRESETS) == "(未知)"
    # 文件缺失也回退
    assert detect_current_preset(tmp_path / "nope.json", _PRESETS) == "(未知)"
