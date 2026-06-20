import pathlib

from llm_manager.config.loader import catalog_domain_models, load
from llm_manager.domain.model import Model, ModelMode

YAML = """
program:
  host: "0.0.0.0"
  port: 8080
  alive_time: 60
  claude_settings_path: "/home/u/.claude/settings.json"
claude_configs:
  GLM:
    ANTHROPIC_BASE_URL: "https://open.bigmodel.cn/api/anthropic"
    ANTHROPIC_DEFAULT_SONNET_MODEL: "glm-5.2"
wake_on_lan:
  broadcast_address: "192.168.50.255"
  mac_address: "a8:b8:e0:08:12:ff"
Local-Models:
  Qwen3.6-27B:
    aliases: ["Qwen3.6-27B", "qwen"]
    mode: "Chat"
    port: 10006
    auto_start: false
    V100:
      required_devices: ["v100"]
      script_path: "Model_startup_script/qwen.bat"
      memory_mb: {v100: 32000}
"""


def _write(tmp_path: pathlib.Path, text: str) -> pathlib.Path:
    p = tmp_path / "config.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_load_returns_appconfig_with_models(tmp_path):
    cfg = load(_write(tmp_path, YAML))
    assert cfg.program.port == 8080
    assert cfg.program.alive_time == 60
    assert "Qwen3.6-27B" in cfg.models
    entry = cfg.models["Qwen3.6-27B"]
    assert entry.aliases == ["Qwen3.6-27B", "qwen"]
    assert set(entry.schemes.keys()) == {"V100"}
    assert entry.schemes["V100"].memory_mb["v100"] == 32000


def test_loads_claude_and_wol_branches(tmp_path):
    cfg = load(_write(tmp_path, YAML))
    assert cfg.wake_on_lan is not None
    assert cfg.wake_on_lan.mac_address == "a8:b8:e0:08:12:ff"
    assert cfg.claude is not None
    assert cfg.claude.presets["GLM"]["ANTHROPIC_BASE_URL"] == "https://open.bigmodel.cn/api/anthropic"
    assert cfg.claude.settings_path == pathlib.Path("/home/u/.claude/settings.json")


def test_catalog_domain_models_conversion(tmp_path):
    cfg = load(_write(tmp_path, YAML))
    models = catalog_domain_models(cfg)
    assert len(models) == 1
    m = models[0]
    assert isinstance(m, Model)
    assert m.primary_name == "Qwen3.6-27B"
    assert m.mode is ModelMode.CHAT
    assert m.port == 10006
    assert "qwen" in m.aliases
