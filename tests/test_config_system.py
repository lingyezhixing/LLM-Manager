"""Phase 2 — Configuration system tests"""
from pathlib import Path

import pytest
from pydantic import ValidationError

from llm_manager.config.loader import ConfigLoadError, YamlConfigLoader
from llm_manager.config.models import (
    AppConfig,
    ModelConfigEntry,
    ModelDeploymentEntry,
    ProgramConfig,
)


class TestYamlLoading:
    def test_load_valid_config(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
program:
  host: "0.0.0.0"
  port: 8080
Local-Models:
  qwen:
    aliases: [qwen, qwen7b]
    mode: Chat
    port: 8081
    rtx_4060:
      required_devices: [rtx_4060]
      script_path: start.sh
      memory_mb: {vram: 6000}
""")
        loader = YamlConfigLoader()
        config = loader.load(config_file)
        assert "qwen" in config.models
        assert config.models["qwen"].aliases == ["qwen", "qwen7b"]

    def test_reject_duplicate_aliases(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
program:
  host: "0.0.0.0"
  port: 8080
Local-Models:
  model-a:
    aliases: [qwen, alias1]
    mode: Chat
    port: 8081
    cpu:
      required_devices: [cpu]
      script_path: start.sh
      memory_mb: {ram: 8000}
  model-b:
    aliases: [llama, alias1]
    mode: Chat
    port: 8082
    cpu:
      required_devices: [cpu]
      script_path: start.sh
      memory_mb: {ram: 8000}
""")
        loader = YamlConfigLoader()
        with pytest.raises(ConfigLoadError):
            loader.load(config_file)

    def test_reject_model_without_deployment(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
program:
  host: "0.0.0.0"
  port: 8080
Local-Models:
  no-deploy:
    aliases: [test]
    mode: Chat
    port: 8083
""")
        loader = YamlConfigLoader()
        with pytest.raises(ConfigLoadError):
            loader.load(config_file)

    def test_empty_models_is_valid(self, tmp_path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text("""
program:
  host: "0.0.0.0"
  port: 8080
""")
        loader = YamlConfigLoader()
        config = loader.load(config_file)
        assert config.models == {}


class TestAdaptiveDeployment:
    def test_select_gpu_when_available(self):
        entry = ModelConfigEntry(
            aliases=["qwen"], mode="Chat", port=8081,
            rtx_4060={"required_devices": ["rtx_4060"], "script_path": "gpu.sh", "memory_mb": {"vram": 6000}},
            cpu={"required_devices": ["cpu"], "script_path": "cpu.sh", "memory_mb": {"ram": 8000}},
        )
        result = entry.select_deployment({"rtx_4060", "cpu"})
        assert result is not None
        name, deployment = result
        assert name == "rtx_4060"
        assert isinstance(deployment, ModelDeploymentEntry)

    def test_fallback_to_cpu_when_gpu_offline(self):
        entry = ModelConfigEntry(
            aliases=["qwen"], mode="Chat", port=8081,
            rtx_4060={"required_devices": ["rtx_4060"], "script_path": "gpu.sh", "memory_mb": {"vram": 6000}},
            cpu={"required_devices": ["cpu"], "script_path": "cpu.sh", "memory_mb": {"ram": 8000}},
        )
        result = entry.select_deployment({"cpu"})
        assert result is not None
        name, _ = result
        assert name == "cpu"

    def test_return_none_when_no_match(self):
        entry = ModelConfigEntry(
            aliases=["qwen"], mode="Chat", port=8081,
            rtx_4060={"required_devices": ["rtx_4060"], "script_path": "gpu.sh", "memory_mb": {"vram": 6000}},
        )
        result = entry.select_deployment({"cpu"})
        assert result is None

    def test_empty_online_devices(self):
        entry = ModelConfigEntry(
            aliases=["qwen"], mode="Chat", port=8081,
            rtx_4060={"required_devices": ["rtx_4060"], "script_path": "gpu.sh", "memory_mb": {"vram": 6000}},
        )
        result = entry.select_deployment(set())
        assert result is None

    def test_multi_device_requirements(self):
        entry = ModelConfigEntry(
            aliases=["qwen"], mode="Chat", port=8081,
            dual={"required_devices": ["rtx_4060", "v100"], "script_path": "dual.sh", "memory_mb": {"vram": 6000}},
            single={"required_devices": ["rtx_4060"], "script_path": "single.sh", "memory_mb": {"vram": 6000}},
        )
        result = entry.select_deployment({"rtx_4060"})
        assert result is not None
        name, _ = result
        assert name == "single"

        result = entry.select_deployment({"rtx_4060", "v100"})
        assert result is not None
        name, _ = result
        assert name == "dual"

    def test_single_deployment_match(self):
        entry = ModelConfigEntry(
            aliases=["qwen"], mode="Chat", port=8081,
            rtx_4060={"required_devices": ["rtx_4060"], "script_path": "gpu.sh", "memory_mb": {"vram": 6000}},
        )
        result = entry.select_deployment({"rtx_4060"})
        assert result is not None
        name, deployment = result
        assert name == "rtx_4060"
        assert deployment.script_path == Path("gpu.sh")


class TestTokenTrackerConfig:
    def test_default_modes(self):
        config = ProgramConfig()
        assert config.should_track_tokens("Chat") is True
        assert config.should_track_tokens("Base") is True
        assert config.should_track_tokens("Embedding") is True
        assert config.should_track_tokens("Reranker") is True
        assert config.should_track_tokens("Unknown") is False

    def test_custom_modes(self):
        config = ProgramConfig(token_tracker=["Chat", "Reranker"])
        assert config.should_track_tokens("Chat") is True
        assert config.should_track_tokens("Reranker") is True
        assert config.should_track_tokens("Embedding") is False
        assert config.should_track_tokens("Base") is False


class TestValidation:
    def test_empty_aliases_rejected(self):
        with pytest.raises(ValidationError):
            AppConfig.model_validate({
                "Local-Models": {
                    "bad-model": {
                        "aliases": [],
                        "mode": "Chat",
                        "port": 8081,
                        "cpu": {"required_devices": ["cpu"], "script_path": "s.sh", "memory_mb": {"ram": 1000}},
                    }
                }
            })

    def test_valid_minimal_config(self):
        config = AppConfig.model_validate({
            "Local-Models": {
                "minimal": {
                    "aliases": ["min"],
                    "mode": "Chat",
                    "port": 8081,
                    "cpu": {"required_devices": ["cpu"], "script_path": "s.sh", "memory_mb": {"ram": 1000}},
                }
            }
        })
        assert "minimal" in config.models
