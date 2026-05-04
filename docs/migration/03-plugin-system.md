# Phase 3: 插件系统迁移

## 目标

将 V2 的设备插件和接口插件实现迁移到 V3 的插件架构中，确保硬件检测、状态读取、健康检查、请求校验等功能完整。

> **注意**：`extract_token_usage()` 已从接口插件中移除。Token 提取逻辑将在 Phase 5 统一到 `utils/tokens.py` + `services/token_tracker.py`，避免分散在各插件中。

## 前置条件

- Phase 2 配置系统已完成（插件目录路径由 `ProgramConfig` 提供）

## V2 → V3 差异分析

### 基类差异

| 接口 | V2 方法 | V3 方法 | 差异 |
|------|---------|---------|------|
| DevicePlugin | `is_online()`, `get_devices_info()` | `is_available()`, `get_status()` | V2 返回 dict，V3 返回 `DeviceStatus` dataclass |
| InterfacePlugin | `health_check()`, `get_supported_endpoints()`, `validate_request()` | `health_check()`, `get_supported_endpoints()`, `validate_request()` | V3 迁移后方法一致，token 提取移至 Phase 5 |

### 设备插件实现差异

| 插件 | V2 | V3 | 差异 |
|------|----|----|------|
| CPU | `cpu.py` — psutil | `cpu.py` — psutil | ✅ 已有 |
| NVIDIA | `rtx_4060.py`, `v100.py` — GPUtil | `nvidia.py` — GPUtil | ⚠️ V3 通用化但需要构造参数 |
| AMD | `amd_780m.py` — pythonnet | `amd.py` — pythonnet | ⚠️ 类似 |

### 关键问题：插件构造函数需要参数

V3 的 `PluginLoader.load()` 调用 `plugin_class(**kwargs)`，但 kwargs 为空。而 `NvidiaDevice.__init__(self, device_name, gpu_index=0)` 和 `AMDDevice.__init__(self, device_name)` 需要参数。

**V2 的解决方案**：V2 通过 `PluginManager` 直接遍历配置中的设备名来构造插件，不用 PluginLoader。

**V3 的解决方案**：改造 `PluginLoader.load()` 支持传入 kwargs，或者将插件改为无参构造 + 配置注入。

## 迁移内容

### 3.1 增强 InterfacePlugin 基类

新增 `validate_request` 方法（V2 有，V3 缺）：

```python
# plugins/base_interface.py
class InterfacePlugin(ABC):
    name: str

    @abstractmethod
    def get_supported_endpoints(self) -> list[str]: ...

    @abstractmethod
    async def health_check(self, port: int, timeout: float = 300.0) -> bool: ...

    @abstractmethod
    def extract_token_usage(self, response: dict) -> TokenUsage: ...

    def validate_request(self, path: str, model_name: str) -> tuple[bool, str]:
        """校验请求路径是否被此接口支持，返回 (是否合法, 错误信息)"""
        supported = self.get_supported_endpoints()
        if path in supported:
            return True, ""
        return False, f"接口 '{self.name}' 不支持端点 '{path}'，支持: {supported}"
```

### 3.2 迁移接口插件实现

#### ChatInterface (plugins/interfaces/chat.py)

V3 已有基本实现，需补充 `validate_request`。V2 的 Chat 接口 `validate_request` 还做了额外校验（如检查 model 字段），但基础路径校验已足够。

#### EmbeddingInterface / RerankerInterface

V3 已有基本实现，同上补充 `validate_request`。

### 3.3 解决设备插件构造参数问题

**方案**：将设备插件改为无参构造 + 延迟配置。V3 的 `PluginLoader.discover()` 扫描目录时实例化插件，此时不知道具体硬件名。

**改造 `NvidiaDevice`**：

```python
class NvidiaDevice(DevicePlugin):
    name = "nvidia"  # 通用名

    def __init__(self):
        self._gpus: list = []  # 延迟检测

    def is_available(self) -> bool:
        try:
            import GPUtil
            self._gpus = GPUtil.getGPUs()
            return len(self._gpus) > 0
        except Exception:
            return False

    def get_status(self) -> DeviceStatus:
        # 返回第一块 GPU 的状态（或聚合多 GPU）
        ...
```

**但问题是**：V2 的配置中 `required_devices: [rtx_4060]` 是具体设备名，而 V3 插件的 `name` 是通用名 `"nvidia"`。需要统一。

**最终方案**：保持 V3 当前设计。设备插件 `name` 对应配置中的 `required_devices`。NVIDIA 插件通过配置文件指定具体名（如 `rtx_4060`），构造时自动检测。

**实际改造**：在 `PluginLoader.load()` 中传入 kwargs：

```python
# plugins/loader.py
def load(self, plugin_class: type, **kwargs) -> object:
    try:
        return plugin_class(**kwargs)
    except TypeError as e:
        raise PluginValidationError(...)
```

然后在 `app.py` 的 `_load_plugins()` 中，根据配置文件中的设备列表构造插件：

```python
# app.py 中 _load_plugins 的设备部分改造
for device_name in config.get_device_names():  # 新增方法
    try:
        instance = plugin_loader.load(NvidiaDevice, device_name=device_name)
        ...
    except Exception:
        ...
```

**但这引入了 V2 的问题**：插件类型和设备名的映射写死在代码里。

**推荐折中方案**：

1. 设备插件保持无参构造，`name` 是类型名（`nvidia`, `amd`, `cpu`）
2. 配置中的 `required_devices` 改用类型名（`nvidia` 而非 `rtx_4060`）
3. 如果未来需要区分多块同类 GPU，在插件内部用 `gpu_index` 区分

这个改动影响配置文件的 `required_devices` 字段语义，需要在迁移时更新 config.yaml。

### 3.4 接口插件 health_check 优化

当前 V3 的每个接口插件的 `health_check` 都创建多个 `httpx.AsyncClient`。与 Phase 1 修复 RequestRouter 类似，但接口插件是轻量级调用，暂不优化。

可在 `InterfacePlugin` 基类中提取通用的 health_check 模板：

```python
# plugins/base_interface.py
class InterfacePlugin(ABC):
    # ... 现有抽象方法 ...

    async def check_server_alive(self, port: int, timeout: float = 3.0) -> bool:
        """通用服务存活检查"""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(f"http://127.0.0.1:{port}/v1/models", timeout=timeout)
                return resp.status_code == 200
        except Exception:
            return False
```

各接口插件继承此方法，在 `health_check` 中先调用 `check_server_alive`，再做接口特定的检查。

## 测试方案

### 测试文件：`tests/test_plugin_system.py`

```python
"""Phase 3 插件系统测试"""
import pytest

from llm_manager.plugins.base_device import DevicePlugin
from llm_manager.plugins.base_interface import InterfacePlugin
from llm_manager.plugins.loader import PluginLoader
from llm_manager.plugins.registry import PluginRegistry
from llm_manager.schemas.device import DeviceStatus, DeviceState


class TestPluginDiscovery:
    """验证插件发现机制"""

    def test_discover_device_plugins(self, tmp_path):
        # 创建测试插件文件
        plugin_dir = tmp_path / "devices"
        plugin_dir.mkdir()
        (plugin_dir / "test_device.py").write_text("""
from llm_manager.plugins.base_device import DevicePlugin
from llm_manager.schemas.device import DeviceStatus

class TestDevice(DevicePlugin):
    name = "test"
    def is_available(self): return True
    def get_status(self): return DeviceStatus(name="test")
""")
        loader = PluginLoader()
        classes = loader.discover(plugin_dir, DevicePlugin)
        assert len(classes) == 1
        assert classes[0].__name__ == "TestDevice"

    def test_skip_base_files(self, tmp_path):
        plugin_dir = tmp_path / "devices"
        plugin_dir.mkdir()
        (plugin_dir / "base_class.py").write_text("class Base: pass")
        (plugin_dir / "_private.py").write_text("class Private: pass")
        loader = PluginLoader()
        classes = loader.discover(plugin_dir, DevicePlugin)
        assert len(classes) == 0


class TestPluginValidation:
    """验证插件校验"""

    def test_valid_plugin_passes(self):
        loader = PluginLoader()
        from llm_manager.plugins.devices.cpu import CPUDevice
        plugin = CPUDevice()
        errors = loader.validate(plugin)
        assert errors == []

    def test_plugin_with_empty_name_fails(self):
        class BadPlugin(DevicePlugin):
            name = ""
            def is_available(self): return True
            def get_status(self): return DeviceStatus(name="")
        loader = PluginLoader()
        errors = loader.validate(BadPlugin())
        assert any("non-empty" in e for e in errors)


class TestCPUDevicePlugin:
    """验证 CPU 设备插件"""

    def test_cpu_is_always_available(self):
        from llm_manager.plugins.devices.cpu import CPUDevice
        cpu = CPUDevice()
        assert cpu.is_available() is True

    def test_cpu_returns_valid_status(self):
        from llm_manager.plugins.devices.cpu import CPUDevice
        cpu = CPUDevice()
        status = cpu.get_status()
        assert isinstance(status, DeviceStatus)
        assert status.name == "cpu"
        assert status.memory_total_mb > 0


class TestInterfacePluginValidateRequest:
    """验证接口插件的请求校验"""

    def test_chat_validates_supported_paths(self):
        from llm_manager.plugins.interfaces.chat import ChatInterface
        chat = ChatInterface()
        ok, _ = chat.validate_request("/v1/chat/completions", "test-model")
        assert ok is True

    def test_chat_rejects_unsupported_path(self):
        from llm_manager.plugins.interfaces.chat import ChatInterface
        chat = ChatInterface()
        ok, err = chat.validate_request("/v1/embeddings", "test-model")
        assert ok is False
        assert "embeddings" in err


class TestPluginRegistry:
    """验证插件注册表"""

    def test_register_and_lookup(self):
        registry = PluginRegistry()
        from llm_manager.plugins.devices.cpu import CPUDevice
        registry.register_device(CPUDevice())
        assert registry.get_device("cpu") is not None
        assert registry.get_device("nonexistent") is None
```

### 测试通过标准

1. `TestPluginDiscovery` — 发现插件、跳过 base/private 文件
2. `TestPluginValidation` — 有效插件通过校验、空名插件被拒绝
3. `TestCPUDevicePlugin` — CPU 插件始终可用且返回有效状态
4. `TestInterfacePluginValidateRequest` — 路径校验正确
5. `TestPluginRegistry` — 注册和查找正常

全部通过后，Phase 3 完成，进入 Phase 4。
