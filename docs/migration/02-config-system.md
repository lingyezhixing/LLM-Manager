# Phase 2: 配置系统迁移

## 目标

将 V2 `ConfigManager` 的核心功能迁移到 V3 的 `config/` 层，重点是**自适应部署配置选择**（根据当前在线设备选择最优启动方案）。

## 前置条件

- Phase 1 数据库层已完成（配置系统不依赖数据库，但 Phase 3+ 依赖此阶段）
- V3 已有 `config/models.py`（Pydantic 模型）和 `config/loader.py`（YAML 加载）

## V2 → V3 差异分析

### 功能差异

| 功能 | V2 (ConfigManager) | V3 (config/) | 状态 |
|------|-------------------|-------------|------|
| YAML 加载 | `load_config()` 手动解析 | `YamlConfigLoader.load()` + Pydantic | ✅ 已有 |
| 别名解析 | `resolve_primary_name()` | `ModelManager.resolve_model_name()` | ✅ 已有（位置不同） |
| 自适应配置 | `get_adaptive_model_config(alias, online_devices)` | 无 | ❌ 需迁移 |
| 配置校验 | `validate_config()` 返回错误列表 | Pydantic 自动校验 | ⚠️ 部分有 |
| token 追踪模式 | `should_track_tokens_for_mode()` | `ProgramConfig.token_tracker` | ⚠️ 有字段但无方法 |
| 设备/接口插件目录 | `get_device_plugin_dir()` | `ProgramConfig.device_plugin_dir` | ✅ 已有 |
| 热重载 | `reload_config()` | 无（`ConfigChanged` 事件已定义但未实现） | ⏳ 后续 |

### 自适应配置机制详解（V2 核心逻辑）

V2 的 `get_adaptive_model_config(alias, online_devices)` 工作流程：

```
config.yaml 中的模型配置:
  Local-Models:
    qwen-7b:
      aliases: [qwen, qwen7b]
      mode: Chat
      port: 8081
      rtx_4060:                    ← 部署方案 1
        required_devices: [rtx_4060]
        script_path: start_gpu.sh
        memory_mb: {vram: 6000}
      cpu:                         ← 部署方案 2（降级）
        required_devices: [cpu]
        script_path: start_cpu.sh
        memory_mb: {ram: 8000}
```

调用 `get_adaptive_model_config("qwen", {"rtx_4060"})` 时：
1. 取出模型的基础配置（aliases, mode, port, auto_start）
2. 遍历非标准字段（rtx_4060, cpu），收集所有部署方案
3. 按配置文件中的顺序（即优先级），找到第一个 `required_devices` 全部在线的方案
4. 返回合并后的配置（基础 + 选中方案的 script_path, memory_mb, required_devices）

**关键特性**：方案的优先级由 YAML 中的书写顺序决定，第一个匹配即使用。

## 迁移内容

### 2.1 增强 Config 模型（config/models.py）

**新增方法**：`ProgramConfig` 增加 token 追踪判断方法。

```python
class ProgramConfig(BaseModel):
    # ... 现有字段 ...
    token_tracker: list[str] = Field(default_factory=lambda: ["Chat", "Base", "Embedding", "Reranker"])

    def should_track_tokens(self, mode: str) -> bool:
        return mode in self.token_tracker
```

### 2.2 新增自适应配置选择（config/models.py）

在 `ModelConfigEntry` 中增加 `select_deployment(online_devices)` 方法：

```python
class ModelConfigEntry(BaseModel):
    aliases: list[str]
    mode: str
    port: int
    auto_start: bool = False

    model_config = {"extra": "allow"}

    def select_deployment(self, online_devices: set[str]) -> tuple[str, ModelDeploymentEntry] | None:
        """根据在线设备选择最优部署方案，返回 (方案名, 方案配置) 或 None"""
        for field_name, field_value in self:
            if field_name in ("aliases", "mode", "port", "auto_start"):
                continue
            if isinstance(field_value, dict):
                try:
                    entry = ModelDeploymentEntry(**field_value)
                    if set(entry.required_devices).issubset(online_devices):
                        return field_name, entry
                except Exception:
                    continue
        return None
```

### 2.3 迁移配置校验（config/loader.py）

V2 的 `validate_config()` 检查：
- `program` 部分有 host/port
- 每个模型有 aliases/mode/port
- 每个设备配置有 required_devices/script_path/memory_mb

V3 的 Pydantic 模型已自动校验大部分字段。额外需要：
- 校验 aliases 列表不为空
- 校验至少有一个部署配置

在 `AppConfig` 上增加 validator：

```python
from pydantic import model_validator

class AppConfig(BaseModel):
    # ... 现有字段 ...

    @model_validator(mode="after")
    def validate_models(self) -> "AppConfig":
        errors = []
        for name, entry in self.models.items():
            if not entry.aliases:
                errors.append(f"模型 '{name}' 的 aliases 为空")
            deployments = entry.get_deployments()
            if not deployments:
                errors.append(f"模型 '{name}' 没有有效的部署配置")
        if errors:
            raise ValueError("配置校验失败: " + "; ".join(errors))
        return self
```

### 2.4 迁移别名唯一性校验

V2 在 `_init_alias_mapping()` 中检查别名重复。V3 中需要在 `AppConfig` validator 中加入：

```python
@model_validator(mode="after")
def validate_unique_aliases(self) -> "AppConfig":
    seen = {}
    for name, entry in self.models.items():
        for alias in entry.aliases:
            if alias in seen:
                raise ValueError(f"别名 '{alias}' 在模型 '{seen[alias]}' 和 '{name}' 中重复")
            seen[alias] = name
    return self
```

## 测试方案

### 测试文件：`tests/test_config_system.py`

```python
"""Phase 2 配置系统测试"""
import pytest
import tempfile
from pathlib import Path

from llm_manager.config.loader import YamlConfigLoader, ConfigLoadError
from llm_manager.config.models import AppConfig, ProgramConfig, ModelConfigEntry


class TestYamlLoading:
    """验证 YAML 配置加载和校验"""

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
    aliases: [llama, alias1]   # alias1 重复
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


class TestAdaptiveDeployment:
    """验证自适应部署配置选择"""

    def test_select_gpu_when_available(self):
        entry = ModelConfigEntry(
            aliases=["qwen"], mode="Chat", port=8081,
            rtx_4060={"required_devices": ["rtx_4060"], "script_path": "gpu.sh", "memory_mb": {"vram": 6000}},
            cpu={"required_devices": ["cpu"], "script_path": "cpu.sh", "memory_mb": {"ram": 8000}},
        )
        result = entry.select_deployment({"rtx_4060", "cpu"})
        assert result is not None
        name, _ = result
        assert name == "rtx_4060"  # GPU 方案优先

    def test_fallback_to_cpu_when_gpu_offline(self):
        entry = ModelConfigEntry(
            aliases=["qwen"], mode="Chat", port=8081,
            rtx_4060={"required_devices": ["rtx_4060"], "script_path": "gpu.sh", "memory_mb": {"vram": 6000}},
            cpu={"required_devices": ["cpu"], "script_path": "cpu.sh", "memory_mb": {"ram": 8000}},
        )
        result = entry.select_deployment({"cpu"})
        assert result is not None
        name, _ = result
        assert name == "cpu"  # 降级到 CPU

    def test_return_none_when_no_match(self):
        entry = ModelConfigEntry(
            aliases=["qwen"], mode="Chat", port=8081,
            rtx_4060={"required_devices": ["rtx_4060"], "script_path": "gpu.sh", "memory_mb": {"vram": 6000}},
        )
        result = entry.select_deployment({"cpu"})
        assert result is None


class TestTokenTrackerConfig:
    """验证 token 追踪配置"""

    def test_default_modes(self):
        config = ProgramConfig()
        assert config.should_track_tokens("Chat") is True
        assert config.should_track_tokens("Unknown") is False

    def test_custom_modes(self):
        config = ProgramConfig(token_tracker=["Chat", "Reranker"])
        assert config.should_track_tokens("Chat") is True
        assert config.should_track_tokens("Embedding") is False
```

### 测试通过标准

1. `TestYamlLoading` — 合法配置加载成功；别名重复、缺少部署配置均被拒绝
2. `TestAdaptiveDeployment` — GPU 优先、降级 CPU、无匹配返回 None
3. `TestTokenTrackerConfig` — 默认模式列表和自定义模式列表均正确判断

全部通过后，Phase 2 完成，进入 Phase 3。
