# 🧠 LLM-Manager v1.0.0

> **重要声明**: 本项目为个人开发的项目，主要用于作者本地环境的LLM模型管理。开源仅为帮助可能有类似需求的用户，**不处理任何功能请求、问题反馈或技术支持**。请使用者根据自身需求自行修改和调试代码。

LLM-Manager 是一个功能强大的大型语言模型管理工具，经过全新架构重构，采用插件化设计，提供更灵活、可扩展的模型管理能力。

## 🎉 v1.0.0 重大更新

### 🏗️ 全新插件化架构
- **完全重写**: 整个系统架构完全重构，采用插件化设计
- **即插即用**: 支持设备和接口插件的自动发现和加载
- **零配置扩展**: 无需修改核心代码即可添加新硬件支持和新接口类型
- **动态热重载**: 支持插件热重载，无需重启应用

### 🔧 核心特性

#### 📦 插件系统
- **设备插件**: 支持 GPU、CPU 等硬件设备的即插即用管理
- **接口插件**: 支持 Chat、Base、Embedding、Reranker 等模式扩展
- **自动发现**: 系统启动时自动扫描并加载所有有效插件
- **类型安全**: 基于抽象基类的插件接口验证

#### 🚀 模型管理
- **多模型支持**: 同时管理多个LLM模型，包括Qwen、GLM、Sakura等系列
- **多模式支持**: 支持 Chat、Base、Embedding、Reranker 四种模型模式
- **智能别名**: 支持模型别名，灵活识别不同名称的模型
- **自动启动**: 配置模型自动启动，系统启动时按需加载
- **健康检查**: 多层健康检查机制，确保服务可用性

#### 💾 智能资源管理
- **插件化GPU管理**: 通过设备插件实现GPU资源的灵活管理
- **动态卸载**: 空闲模型自动卸载，释放资源
- **显存优化**: 手动配置显存需求，程序根据配置动态分配
- **多设备支持**: 支持多GPU、多设备环境下的资源分配

#### 🌐 统一接口
- **OpenAI兼容API**: 提供与OpenAI API兼容的统一接口
- **多模式路由**: 支持不同接口模式的专门路由和验证
- **流式响应支持**: 支持流式和非流式响应
- **自动模型加载**: 请求时自动启动对应模型
- **请求追踪**: 实时追踪模型请求状态和数量

#### 🖥️ 现代化管理界面
- **实时监控**: 显示设备和模型运行状态
- **模型控制面板**: 可视化启动/停止模型
- **实时日志查看**: 查看模型启动和运行日志
- **系统托盘**: Windows系统托盘快捷操作

## 🏗️ 系统架构

### 核心组件

```
LLM-Manager/
├── main.py                    # 主程序入口
├── core/                      # 核心模块
│   ├── model_controller.py    # 模型控制器
│   ├── api_server.py          # API服务器
│   ├── webui.py              # WebUI服务器
│   ├── plugin_system.py      # 插件系统
│   └── tray.py               # 系统托盘
├── plugins/                   # 插件目录
│   ├── devices/              # 设备插件
│   │   ├── Base_Class.py     # 设备插件基类
│   │   ├── cpu.py           # CPU设备插件
│   │   ├── rtx_4060.py      # RTX4060插件
│   │   └── v100.py          # V100插件
│   └── interfaces/          # 接口插件
│       ├── Base_Class.py     # 接口插件基类
│       ├── chat.py          # Chat模式插件
│       ├── embedding.py     # Embedding模式插件
│       └── reranker.py      # Reranker模式插件
├── utils/                     # 工具模块
│   └── logger.py             # 日志工具
├── webui/                     # WebUI模块
│   └── server.py             # WebUI服务器
├── config-rebuild.json        # 配置文件
├── requirements.txt           # Python依赖
├── LLM-Manager.bat          # 启动脚本
└── Model_startup_script/     # 模型启动脚本
```

### 插件系统架构

#### 设备插件
- **作用**: 管理硬件设备（GPU、CPU等）
- **基类**: `DevicePlugin`
- **标识符**: `device_name` 属性
- **必需方法**: `is_online()`, `get_memory_info()`

#### 接口插件
- **作用**: 定义模型接口类型（Chat、Base、Embedding、Reranker等）
- **基类**: `InterfacePlugin`
- **标识符**: `interface_name` 属性
- **必需方法**: `health_check()`, `get_supported_endpoints()`, `validate_request()`

## ⚙️ 配置要求

**重要**: 在使用本项目前，您必须修改以下代码以适配您的本地环境：

### 1. 配置文件

使用新的配置文件格式 `config-rebuild.json`：

```json
{
  "program": {
    "host": "0.0.0.0",
    "port": 8080,
    "Disable_GPU_monitoring": false,
    "alive_time": 60,
    "device_plugin_dir": "plugins/devices",
    "interface_plugin_dir": "plugins/interfaces",
    "log_level": "INFO"
  },
  "Qwen3-Coder-30B-A3B-Instruct-UD-64K": {
    "aliases": [
      "Qwen3-Coder-30B-A3B-Instruct-64K",
      "Qwen3-Coder-30B-A3B-Instruct"
    ],
    "mode": "Chat",
    "port": 10001,
    "auto_start": false,
    "RTX4060-V100": {
      "required_devices": [
        "rtx 4060",
        "v100"
      ],
      "bat_path": "Model_startup_script\\Qwen3-Coder-30B-A3B-Instruct-UD-64K.bat",
      "memory_mb": {
        "rtx 4060": 6144,
        "v100": 16000
      }
    },
    "RTX4060": {
      "required_devices": [
        "rtx 4060"
      ],
      "bat_path": "Model_startup_script\\RTX4060\\Qwen3-Coder-30B-A3B-Instruct-64K-RTX4060.bat",
      "memory_mb": {
        "rtx 4060": 5120
      }
    }
  }
}
```

### 2. 设备插件配置

新架构使用设备插件来管理硬件，无需在代码中硬编码GPU信息：

```python
# plugins/devices/my_gpu.py
from plugins.devices.Base_Class import DevicePlugin

class MyGPUDevice(DevicePlugin):
    def __init__(self):
        super().__init__("my_gpu")  # 设备标识符

    def is_online(self) -> bool:
        # 实现设备在线检查逻辑
        return True

    def get_memory_info(self) -> Tuple[int, int, int]:
        # 返回 (总内存, 可用内存, 已用内存) 单位MB
        return 16384, 8192, 8192
```

### 3. 接口插件配置

新接口模式可以通过插件添加：

```python
# plugins/interfaces/my_mode.py
from plugins.interfaces.Base_Class import InterfacePlugin

class MyModeInterface(InterfacePlugin):
    def __init__(self, model_manager=None):
        super().__init__("MyMode", model_manager)

    def health_check(self, model_alias: str, port: int, start_time: float, timeout_seconds: int):
        # 实现健康检查逻辑
        return True, "MyMode接口健康"

    def get_supported_endpoints(self) -> set:
        return {"v1/mymode/completions"}

    def validate_request(self, path: str, model_alias: str):
        if "v1/mymode/completions" not in path:
            return False, f"模型 '{model_alias}' 是 'MyMode' 模式, 只支持 MyMode 接口"
        return True, ""
```

### 4. 模型启动脚本配置

每个模型都需要独立的启动脚本：

```batch
@echo off
chcp 65001 >nul
cd /d "%~dp0"
call conda activate YOUR_ACTUAL_CONDA_ENV
python -m vllm.entrypoints.openai.api_server \
  --model /path/to/your/model \
  --port 10001 \
  --tensor-parallel-size 2 \
  --gpu-memory-utilization 0.9
```

## 🚀 快速开始

### 系统要求
- Windows操作系统
- Python 3.8+
- NVIDIA GPU（推荐多GPU环境）
- Conda环境管理器

### 安装步骤

1. **克隆项目**
   ```bash
   git clone <repository-url>
   cd LLM-Manager
   ```

2. **创建Conda环境**
   ```bash
   conda create -n LLM-Manager python=3.10
   conda activate LLM-Manager
   ```

3. **安装依赖**
   ```bash
   pip install -r requirements.txt
   ```

4. **配置模型和环境**
   - 按照"配置要求"修改 `config-rebuild.json` 文件
   - 创建模型启动脚本
   - 如需自定义设备或接口，在 `plugins/` 目录下创建相应插件

5. **启动系统**
   ```bash
   # 方式1：直接运行Python脚本
   python main.py

   # 方式2：使用批处理文件（推荐）
   LLM-Manager.bat
   ```

### 启动验证

系统启动后会：
1. **自动发现插件**: 扫描并加载所有设备和接口插件
2. **启动API服务和Web管理界面**（默认：http://0.0.0.0:8080）
3. **在系统托盘显示管理图标**
4. **自动启动模型**: 启动配置为自动启动的模型

## 📖 使用指南

### Web界面操作

1. **访问管理界面**
   - 打开浏览器访问：`http://127.0.0.1:8080`
   - 界面显示设备监控和模型控制面板

2. **模型管理**
   - **启动模型**: 点击"启动"按钮启动对应模型
   - **停止模型**: 点击"停止"按钮停止运行中的模型
   - **查看状态**: 实时显示模型状态和待处理请求数
   - **模式识别**: 界面显示模型模式（💬 Chat, 📝 Base, 🔍 Embedding, 🔄 Reranker）

3. **设备监控**
   - **实时状态**: 显示所有已加载设备插件的状态
   - **内存信息**: 显示设备内存使用情况
   - **插件信息**: 显示插件类型和健康状态

### API使用

#### 获取模型列表
```bash
curl http://localhost:8080/v1/models
```

#### 聊天补全请求
```bash
curl -X POST http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-Coder-30B-A3B-Instruct",
    "messages": [{"role": "user", "content": "Hello!"}],
    "max_tokens": 100
  }'
```

#### 文本补全请求（Base模式）
```bash
curl -X POST http://localhost:8080/v1/completions \
  -H "Content-Type: application/json" \
  -d '{
    "model": "base-model-name",
    "prompt": "Hello!",
    "max_tokens": 100
  }'
```

#### 嵌入向量请求（Embedding模式）
```bash
curl -X POST http://localhost:8080/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{
    "model": "Qwen3-Embedding-8B",
    "input": "Hello, world!",
    "encoding_format": "float"
  }'
```

#### 重排序请求（Reranker模式）
```bash
curl -X POST http://localhost:8080/v1/rerank \
  -H "Content-Type: application/json" \
  -d '{
    "model": "bge-reranker-v2-m3",
    "query": "What is artificial intelligence?",
    "documents": [
      "Artificial intelligence is a branch of computer science.",
      "Machine learning is a subset of AI.",
      "Deep learning uses neural networks."
    ],
    "top_n": 2
  }'
```

### 插件开发

#### 创建设备插件

1. **创建插件文件**
```python
# plugins/devices/my_device.py
from typing import Tuple
from plugins.devices.Base_Class import DevicePlugin

class MyDevice(DevicePlugin):
    def __init__(self):
        super().__init__("my_device")

    def is_online(self) -> bool:
        return True

    def get_memory_info(self) -> Tuple[int, int, int]:
        return 16384, 8192, 8192
```

2. **重启系统**
```bash
python main.py
```

#### 创建接口插件

1. **创建插件文件**
```python
# plugins/interfaces/my_interface.py
import openai
import time
from typing import Tuple
from plugins.interfaces.Base_Class import InterfacePlugin

class MyInterface(InterfacePlugin):
    def __init__(self, model_manager=None):
        super().__init__("MyInterface", model_manager)

    def health_check(self, model_alias: str, port: int, start_time: float = None, timeout_seconds: int = 300) -> Tuple[bool, str]:
        try:
            client = openai.OpenAI(base_url=f"http://127.0.0.1:{port}/v1", api_key="dummy-key")
            client.models.list(timeout=3.0)
            return True, "MyInterface接口健康"
        except Exception as e:
            return False, f"MyInterface接口异常: {e}"

    def get_supported_endpoints(self) -> set:
        return {"v1/myinterface/completions"}

    def validate_request(self, path: str, model_alias: str) -> Tuple[bool, str]:
        if "v1/myinterface/completions" not in path:
            return False, f"模型 '{model_alias}' 是 'MyInterface' 模式, 只支持 MyInterface 接口"
        return True, ""
```

2. **重启系统**
```bash
python main.py
```

## 🔧 高级配置

### 插件配置

新插件创建后，可以在配置文件中使用：

```json
{
  "MyModel": {
    "aliases": ["mymodel"],
    "mode": "MyInterface",
    "port": 10015,
    "auto_start": false,
    "MyDevice_Config": {
      "required_devices": ["my_device"],
      "bat_path": "scripts/mymodel.bat",
      "memory_mb": {
        "my_device": 8192
      }
    }
  }
}
```

### 插件管理

#### 查看已加载插件
```python
from core.plugin_system import PluginManager

manager = PluginManager()
status = manager.get_plugin_status()
print(status)
```

#### 热重载插件
```python
from core.plugin_system import PluginManager

manager = PluginManager()
# 重新加载所有插件
manager.reload_plugins()
```

#### 验证插件
```python
from core.plugin_system import PluginManager

manager = PluginManager()
# 验证插件文件结构
result = manager.validate_plugin_structure("plugins/devices/my_device.py")
print(result)
```

### 性能优化

1. **插件优化**
   - 限制插件数量：设备插件不超过20个，接口插件不超过10个
   - 优化插件加载时间：每个插件加载时间控制在10-100ms
   - 合理管理插件内存：每个插件实例约占用1-5MB内存

2. **并发控制**
   - 使用全局加载锁确保模型顺序加载
   - 请求计数器追踪并发请求数
   - 智能资源分配和释放

3. **日志管理**
   - 自动管理日志文件：`logs/` 目录
   - 自动清理：保留最近9个日志文件
   - 文件命名：`LLM-Manager_YYYYMMDDHHMMSS.log`

## 🐛 故障排除

### 常见问题

1. **插件加载失败**
   - 检查插件文件是否在正确的目录中
   - 确保文件名以 `.py` 结尾
   - 验证插件类继承正确的基类
   - 检查是否实现了所有抽象方法

2. **设备检测失败**
   - 确保设备插件正确实现 `is_online()` 方法
   - 检查设备是否被其他程序占用
   - 查看启动日志中的设备检测结果

3. **模型启动失败**
   - 检查模型启动脚本路径和内容
   - 验证端口是否被占用
   - 查看模型日志获取详细错误信息
   - 确认设备插件是否正常加载

4. **API请求失败**
   - 检查模型是否正常运行
   - 验证请求格式和参数
   - 查看API服务日志
   - 确认接口插件是否正确加载

### 调试模式

启用详细日志：
```python
# 在config-rebuild.json中设置
{
  "program": {
    "log_level": "DEBUG"
  }
}
```

### 插件调试

单独测试插件：
```bash
# 使用插件测试工具
python -c "from core.plugin_system import PluginManager; pm = PluginManager(); print(pm.load_all_plugins())"
```

## 📊 插件开发最佳实践

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
def health_check(self, model_alias: str, port: int, start_time: float, timeout_seconds: int):
    if time.time() - start_time > timeout_seconds:
        return False, "健康检查超时"
    return self._deep_health_check(port)
```

## 🤝 项目声明

### 开发说明
- **个人项目**: 本项目完全为个人使用开发，未考虑通用性
- **不提供支持**: **不处理任何Issue、Pull Request或技术支持请求**
- **自行修改**: 使用者必须根据自身环境修改相关代码
- **仅供参考**: 代码结构和实现方式仅作为参考

### 使用建议
1. **仔细阅读**: 使用前请仔细阅读"配置要求"章节
2. **逐步调试**: 建议逐步修改和测试每个配置项
3. **备份重要数据**: 修改前备份重要配置文件
4. **理解原理**: 建议理解代码原理后再进行修改

### 免责声明
本项目按"原样"提供，**不提供任何明示或暗示的保证**。使用者需自行承担使用风险，开发者不对任何损失或问题负责。

## 📄 许可证

本项目采用 [MIT许可证](LICENSE)。

## 📝 更新日志

### v1.0.0 - 2025-09-22
#### 重大更新
- **完全重构**: 整个系统架构完全重写，采用插件化设计
- **插件系统**: 实现即插即用的设备和接口插件系统
- **零配置扩展**: 无需修改核心代码即可添加新硬件支持和新接口类型
- **动态热重载**: 支持插件热重载，无需重启应用

#### 技术改进
- **模块化设计**: 核心功能模块化，便于维护和扩展
- **插件管理**: 统一的插件管理器，支持插件发现、加载、验证
- **类型安全**: 基于抽象基类的插件接口验证
- **错误隔离**: 单个插件错误不影响系统运行

#### 新增特性
- **设备插件**: 支持GPU、CPU等硬件设备的插件化管理
- **接口插件**: 支持Chat、Base、Embedding、Reranker等模式扩展
- **自动发现**: 系统启动时自动扫描并加载所有有效插件
- **状态监控**: 实时监控插件状态和健康情况

#### 配置改进
- **新配置格式**: 采用 `config-rebuild.json` 新格式
- **插件配置**: 支持插件相关的配置项
- **优先级机制**: 保持原有的多设备优先级配置机制

#### 向后兼容
- **API兼容**: 保持与原有OpenAI API的完全兼容
- **配置迁移**: 提供从旧配置格式的迁移支持
- **功能保持**: 保持所有原有功能的正常工作

#### 性能优化
- **启动速度**: 优化系统启动时间和插件加载速度
- **内存使用**: 优化插件内存管理和资源使用
- **并发处理**: 改进模型加载和请求处理的并发机制

### v0.2.1 - 2025-09-20
#### 新增功能
- **Reranker模型支持**: 完整支持Reranker模式模型的配置和管理
- **重排序API端点**: 添加 `/v1/rerank` 端点的代理和验证功能
- **模式路由扩展**: 支持Chat、Base、Embedding、Reranker四种模式的专门路由
- **健康检查增强**: 支持reranker模型的功能性健康检查

#### 技术改进
- **API兼容性**: 严格验证reranker模式与API端点的兼容性
- **Web界面更新**: 添加reranker模式的显示图标（🔄）
- **配置管理**: 支持reranker模型的配置文件管理
- **错误处理**: 增强reranker模型启动和运行时的错误处理

### v0.2.0 - 2025-09-08
#### 重大更新
- **优先级配置系统**: 全新的GPU配置优先级机制，支持多环境自适应
- **动态GPU检测**: 移除系统启动GPU依赖，实现运行时动态检测
- **智能配置选择**: 根据实际GPU状态自动选择最佳配置方案
- **灵活多GPU支持**: 支持任意GPU组合和优先级配置

#### 技术改进
- **启动逻辑优化**: 系统启动不再依赖GPU检测，提高启动速度
- **配置格式重构**: 采用新的优先级配置格式，支持多种GPU组合
- **实时适配**: GPU状态变化时自动重新配置和选择
- **日志增强**: 详细记录配置选择过程，便于调试和监控

### v0.1.1 - 2025-09-06
#### 新增功能
- **Embedding模型支持**: 添加对Embedding模式模型的完整支持
- **多模式路由**: 实现Chat、Base、Embedding三种模式的专门路由验证
- **Web界面增强**: 在模型控制面板显示模式标识和图标
- **健康检查扩展**: 支持embedding模型的功能性健康检查

#### 技术改进
- **API兼容性**: 严格验证模型模式与API端点的兼容性
- **配置管理**: 支持embedding模型的配置文件管理
- **错误处理**: 增强embedding模型启动和运行时的错误处理

---

**LLM-Manager v1.0.0** - 插件化的LLM模型管理平台