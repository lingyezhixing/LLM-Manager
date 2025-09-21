# LLM-Manager 即插即用插件系统指南

## 概述

LLM-Manager 现在支持真正的即插即用插件系统，无需修改核心代码即可添加新的设备插件和接口插件。

## 系统架构

### 1. 自动插件发现机制

- **扫描插件目录**: 系统启动时自动扫描 `plugins/devices/` 和 `plugins/interfaces/` 目录
- **动态加载**: 使用 Python 动态导入机制自动加载所有有效的插件文件
- **类验证**: 自动验证插件类是否正确实现抽象基类
- **实例化**: 自动创建插件实例并注册到系统中

### 2. 插件类型

#### 设备插件 (`plugins/devices/`)
- **作用**: 管理硬件设备（GPU、CPU等）
- **基类**: `DevicePlugin`
- **标识符**: `device_name` 属性
- **必需方法**: `is_online()`, `get_memory_info()`

#### 接口插件 (`plugins/interfaces/`)
- **作用**: 定义模型接口类型（Chat、Base、Embedding、Reranker等）
- **基类**: `InterfacePlugin`
- **标识符**: `interface_name` 属性
- **必需方法**: `health_check()`, `get_supported_endpoints()`, `validate_request()`

## 创建新插件

### 创建设备插件

1. **创建插件文件**
```python
# plugins/devices/my_gpu.py
import logging
from typing import Tuple
from plugins.devices.Base_Class import DevicePlugin

logger = logging.getLogger(__name__)

class MyGPUDevice(DevicePlugin):
    def __init__(self):
        super().__init__("my_gpu")  # 设备标识符
        logger.info(f"初始化MyGPU设备: {self.device_name}")

    def is_online(self) -> bool:
        # 实现设备在线检查逻辑
        return True

    def get_memory_info(self) -> Tuple[int, int, int]:
        # 返回 (总内存, 可用内存, 已用内存) 单位MB
        return 16384, 8192, 8192
```

2. **放置插件文件**
```
plugins/
└── devices/
    └── my_gpu.py  # 新设备插件
```

3. **重启系统**
```bash
# 重启LLM-Manager，新插件会自动加载
python main.py
```

### 创建接口插件

1. **创建插件文件**
```python
# plugins/interfaces/my_mode.py
import openai
import time
from typing import Tuple
from fastapi import FastAPI
from plugins.interfaces.Base_Class import InterfacePlugin

class MyModeInterface(InterfacePlugin):
    def __init__(self, model_manager=None):
        super().__init__("MyMode", model_manager)

    def health_check(self, model_alias: str, port: int, start_time: float = None, timeout_seconds: int = 300) -> Tuple[bool, str]:
        # 实现健康检查逻辑
        try:
            client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
            client.models.list(timeout=3.0)
            return True, "MyMode接口健康"
        except Exception as e:
            return False, f"MyMode接口异常: {e}"

    def get_supported_endpoints(self) -> set:
        """获取该接口支持的API端点"""
        return {"v1/mymode/completions"}

    def validate_request(self, path: str, model_alias: str) -> Tuple[bool, str]:
        """验证请求路径是否适合该接口类型"""
        if "v1/mymode/completions" not in path:
            return False, f"模型 '{model_alias}' 是 'MyMode' 模式, 只支持 MyMode 接口"
        return True, ""
```

2. **放置插件文件**
```
plugins/
└── interfaces/
    └── my_mode.py  # 新接口插件
```

3. **重启系统**
```bash
# 重启LLM-Manager，新插件会自动加载
python main.py
```

## 配置文件使用

新插件创建后，可以在配置文件中使用：

### 设备插件配置示例
```json
{
  "MyModel": {
    "aliases": ["mymodel"],
    "mode": "MyMode",  # 使用新的接口模式
    "port": 10015,
    "auto_start": false,
    "MyGPU_Config": {
      "required_devices": ["my_gpu"],  // 使用新的设备
      "bat_path": "scripts/mymodel.bat",
      "memory_mb": {
        "my_gpu": 8192  // 新设备的内存需求
      }
    }
  }
}
```

## 插件开发最佳实践

### 1. 错误处理
```python
def is_online(self) -> bool:
    try:
        # 设备检查逻辑
        return True
    except Exception as e:
        logger.error(f"设备检查失败: {e}")
        return False
```

### 2. 日志记录
```python
def __init__(self):
    super().__init__("my_device")
    logger.info(f"设备插件初始化: {self.device_name}")
```

### 3. 资源管理
```python
def cleanup(self):
    # 清理资源
    logger.info(f"清理设备资源: {self.device_name}")
```

### 4. 健康检查
```python
def health_check(self, model_alias: str, port: int, start_time: float, timeout_seconds: int) -> Tuple[bool, str]:
    # 基础检查
    if time.time() - start_time > timeout_seconds:
        return False, "健康检查超时"

    # 深度检查
    return self._deep_health_check(port)
```

## 测试插件

### 1. 单独测试插件
```bash
# 使用插件测试工具
python -c "from core.plugin_system import PluginManager; pm = PluginManager(); print(pm.load_all_plugins())"
```

### 2. 验证插件加载
```python
from core.plugin_system import PluginManager

manager = PluginManager()
plugins = manager.load_all_plugins()
print(f"加载的设备插件: {list(plugins['device_plugins'].keys())}")
print(f"加载的接口插件: {list(plugins['interface_plugins'].keys())}")
```

### 3. 使用便捷函数
```python
from core.plugin_system import load_device_plugins, load_interface_plugins

# 快速加载设备插件
device_plugins = load_device_plugins()

# 快速加载接口插件
interface_plugins = load_interface_plugins(model_manager=model_manager)
```

### 4. 检查插件状态
```python
status = manager.get_plugin_status()
print(f"在线设备: {[d for d, info in status['device_plugins'].items() if info['online']]}")
```

## 故障排除

### 1. 插件未被加载
- 检查文件是否在正确的插件目录中
- 确保文件名以 `.py` 结尾
- 验证插件类继承正确的基类
- 检查是否实现了所有抽象方法

### 2. 导入错误
- 确保所有导入的模块存在
- 检查相对导入是否正确
- 验证依赖包是否已安装

### 3. 运行时错误
- 检查插件方法的实现逻辑
- 验证错误处理是否完善
- 确保资源正确管理

### 4. 配置问题
- 确保配置文件中的插件名称正确
- 检查设备名称和接口名称是否匹配
- 验证内存需求设置是否合理

## 热重载支持

系统支持插件热重载，无需重启整个应用：

```python
from utils.plugin_manager import PluginManager

manager = PluginManager()
# 重新加载所有插件
manager.reload_plugins()
```

## 性能考虑

### 1. 插件数量
- 设备插件：建议不超过20个
- 接口插件：建议不超过10个

### 2. 启动时间
- 插件发现：通常 < 1秒
- 插件加载：每个插件约 10-100ms

### 3. 内存使用
- 每个插件实例约占用 1-5MB 内存
- 大量插件时注意系统内存限制

## 总结

LLM-Manager 的即插即用插件系统提供了：

1. **零配置**: 新插件自动发现和加载
2. **类型安全**: 抽象基类确保接口一致性
3. **灵活扩展**: 支持任意数量的设备和接口
4. **热重载**: 无需重启即可更新插件
5. **错误隔离**: 单个插件错误不影响系统运行

开发者可以轻松添加新的硬件支持和模型接口，无需修改核心代码。