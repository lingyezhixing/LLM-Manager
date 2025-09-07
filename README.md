# 🧠 LLM-Manager

> **重要声明**: 本项目为个人开发的项目，主要用于作者本地环境的LLM模型管理。开源仅为帮助可能有类似需求的用户，**不处理任何功能请求、问题反馈或技术支持**。请使用者根据自身需求自行修改和调试代码。

LLM-Manager 是一个功能强大的大型语言模型管理工具，旨在帮助用户高效地管理和部署多个LLM模型。该系统提供了统一的Web界面、API接口和智能资源管理功能，支持自动模型加载、显存优化和实时监控。

## 🌟 核心特性

### 🔧 模型管理
- **多模型支持**: 同时管理多个LLM模型，包括Qwen、GLM、Sakura等系列
- **多种模式支持**: 支持Chat、Base和Embedding三种模型模式
- **智能别名系统**: 支持模型别名，灵活识别不同名称的模型
- **自动启动**: 配置模型自动启动，系统启动时按需加载
- **健康检查**: 自动检测模型状态，确保服务可用性

### 💾 智能资源管理
- **GPU显存优化**: 手动配置显存需求，程序根据配置动态分配
- **动态卸载**: 空闲模型自动卸载，释放资源
- **显存不足处理**: 智能停止空闲模型为新模型腾出空间
- **多GPU支持**: 支持多GPU环境下的资源分配

### 🌐 统一接口
- **OpenAI兼容API**: 提供与OpenAI API兼容的统一接口
- **多模式路由**: 支持Chat、Base和Embedding模式的专门路由
- **流式响应支持**: 支持流式和非流式响应
- **自动模型加载**: 请求时自动启动对应模型
- **请求追踪**: 实时追踪模型请求状态和数量

### 🖥️ 可视化管理
- **实时GPU监控**: 显示GPU使用率和显存状态
- **模型控制面板**: 可视化启动/停止模型
- **实时日志查看**: 查看模型启动和运行日志
- **系统托盘**: Windows系统托盘快捷操作

## ⚙️ 本地化配置要求

**重要**: 在使用本项目前，您必须修改以下代码以适配您的本地环境：

### 1. GPU环境配置

系统现在支持动态GPU检测和优先级配置，无需修改代码：

#### 新特性：优先级配置系统
- **动态GPU检测**: 系统启动时自动检测可用GPU
- **优先级机制**: 按配置文件中的优先级顺序尝试不同配置方案
- **灵活适配**: 根据实际GPU状态自动选择最佳配置

#### 配置示例
```json
"Qwen3-Coder-30B-A3B-Instruct-UD-64K": {
  "aliases": ["Qwen3-Coder-30B-A3B-Instruct-64K", "Qwen3-Coder-30B-A3B-Instruct"],
  "mode": "Chat",
  "port": 10001,
  "auto_start": false,
  "RTX4060-V100": {
    "required_gpus": ["rtx 4060", "v100"],
    "bat_path": "Model_startup_script\\Qwen3-Coder-30B-A3B-Instruct-UD-64K.bat",
    "gpu_mem_mb": {
      "rtx 4060": 6144,
      "v100": 16000
    }
  },
  "RTX4060": {
    "required_gpus": ["rtx 4060"],
    "bat_path": "Model_startup_script\\Qwen3-Coder-30B-A3B-Instruct-RTX4060.bat",
    "gpu_mem_mb": {
      "rtx 4060": 5120,
      "v100": 0
    }
  }
}
```

#### 配置说明
- **优先级顺序**: 按配置文件中的顺序尝试（先RTX4060-V100，后RTX4060）
- **GPU匹配**: 只有当所有required_gpus都可用时才选择该配置
- **灵活配置**: 可根据实际GPU环境添加不同的配置方案

**GPU名称格式**: 去除"NVIDIA"、"GeForce"等前缀，使用简化格式。您可以通过以下命令查看您的GPU名称：
```bash
nvidia-smi --query-gpu=name --format=csv,noheader
```

### 2. 显存配置说明 (config.json)

**核心机制**: 本程序不会自动检测模型实际需要的显存，而是根据您手动配置的 `gpu_mem_mb` 数值来进行显存分配和管理。

```json
"gpu_mem_mb": {
  "rtx 4060": 6144,   // 该模型在RTX 4060上需要6GB显存
  "v100": 16000       // 该模型在V100上需要16GB显存
}
```

**优先级配置下的显存管理**:
- 在每个配置方案中独立配置显存需求
- 不同配置方案可以使用不同的显存分配策略
- 设置为0表示该GPU不分配显存

**配置方法**:
1. 启动您的模型服务脚本
2. 观察模型实际占用的显存（使用 `nvidia-smi`）
3. 在配置文件中填写对应的显存数值
4. 测试启动，如遇显存不足则调整数值

**注意事项**:
- 设置为0表示该GPU不分配显存
- 数值单位为MB（兆字节）
- 需要为每个模型单独配置和测试
- 虽然手动配置较麻烦，但这种方式支持任何提供OpenAI兼容接口的框架

### 3. 模型启动脚本配置

每个模型都需要独立的启动脚本，您需要：

1. **修改模型路径**: 将脚本中的模型路径改为您的本地模型路径
2. **调整Conda环境**: 修改为您实际的Conda环境名称
3. **配置端口和GPU**: 根据您的环境调整端口分配和GPU参数

示例脚本修改：
```batch
@echo off
chcp 65001 >nul
cd /d "%~dp0"
call conda activate YOUR_ACTUAL_CONDA_ENV  # 修改为您的环境
python -m vllm.entrypoints.openai.api_server \
  --model /your/local/model/path  # 修改为您的模型路径
  --port 10001 \
  --tensor-parallel-size 2 \
  --gpu-memory-utilization 0.9
```

### 4. 网络和端口配置 (config.json)

根据您的网络环境修改：

```json
{
  "program": {
    "openai_host": "0.0.0.0",  // 如需外部访问改为0.0.0.0
    "openai_port": 8080,        // 修改为您的可用端口
    "webui_host": "127.0.0.1",  // Web界面访问地址
    "webui_port": 10000,       // Web界面端口
    "alive_time": 60           // 模型空闲超时时间（分钟）
  },
  
  // 优先级配置示例
  "Qwen3-32B-Instruct": {
    "aliases": ["Qwen3-32B-Instruct", "qwen3-32b", "qwen-32b"],
    "mode": "Chat",
    "port": 10001,
    "auto_start": false,
    "RTX4090-RTX3090": {
      "required_gpus": ["rtx 4090", "rtx 3090"],
      "bat_path": "Model_startup_script\\Qwen3-32B-Instruct-Dual-GPU.bat",
      "gpu_mem_mb": {
        "rtx 4090": 8192,
        "rtx 3090": 8192
      }
    },
    "RTX4090": {
      "required_gpus": ["rtx 4090"],
      "bat_path": "Model_startup_script\\Qwen3-32B-Instruct-RTX4090-Only.bat",
      "gpu_mem_mb": {
        "rtx 4090": 16384,
        "rtx 3090": 0
      }
    },
    "RTX3090": {
      "required_gpus": ["rtx 3090"],
      "bat_path": "Model_startup_script\\Qwen3-32B-Instruct-RTX3090-Only.bat",
      "gpu_mem_mb": {
        "rtx 4090": 0,
        "rtx 3090": 16384
      }
    }
  },
  
  "GLM-4-9B-Chat": {
    "aliases": ["GLM-4-9B-Chat", "glm-4-9b", "glm4-9b"],
    "mode": "Chat",
    "port": 10002,
    "auto_start": true,
    "RTX4090": {
      "required_gpus": ["rtx 4090"],
      "bat_path": "Model_startup_script\\GLM-4-9B-Chat-RTX4090.bat",
      "gpu_mem_mb": {
        "rtx 4090": 4096,
        "rtx 3090": 0
      }
    },
    "RTX3090": {
      "required_gpus": ["rtx 3090"],
      "bat_path": "Model_startup_script\\GLM-4-9B-Chat-RTX3090.bat",
      "gpu_mem_mb": {
        "rtx 4090": 0,
        "rtx 3090": 4096
      }
    }
  },
  
  "Qwen3-Embedding-8B": {
    "aliases": ["Qwen3-Embedding-8B", "Qwen3-Embedding", "qwen3-embedding"],
    "mode": "Embedding",
    "port": 8081,
    "auto_start": false,
    "RTX4090": {
      "required_gpus": ["rtx 4090"],
      "bat_path": "Model_startup_script\\Qwen3-Embedding-8B-RTX4090.bat",
      "gpu_mem_mb": {
        "rtx 4090": 4096,
        "rtx 3090": 0
      }
    },
    "RTX3090": {
      "required_gpus": ["rtx 3090"],
      "bat_path": "Model_startup_script\\Qwen3-Embedding-8B-RTX3090.bat",
      "gpu_mem_mb": {
        "rtx 4090": 0,
        "rtx 3090": 4096
      }
    }
  }
}
```

### 5. 可选：移除系统托盘功能

如不需要系统托盘功能，可以注释或删除相关代码：
- `main.py` 中的系统托盘初始化代码（第82-107行）
- 相关的托盘图标文件（如果存在）

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
   - 按照"本地化配置要求"修改相关代码
   - 编辑 `config.json` 文件，配置您的模型信息
   - 创建模型启动脚本

5. **启动系统**
   ```bash
   # 方式1：直接运行Python脚本
   python main.py
   
   # 方式2：使用批处理文件（推荐）
   LLM-Manager.bat
   ```

### 启动验证

系统启动后会：
1. **动态GPU检测**: 自动检测当前可用GPU，无需预配置
2. **启动Web管理界面**（默认：http://127.0.0.1:10000）
3. **启动API服务**（默认：http://0.0.0.0:8080）
4. **在系统托盘显示管理图标**
5. **按优先级自动适配**: 根据实际GPU状态选择最佳配置方案

## 📖 使用指南

### Web界面操作

1. **访问管理界面**
   - 打开浏览器访问：`http://127.0.0.1:10000`
   - 界面显示GPU监控和模型控制面板

2. **模型管理**
   - **启动模型**: 点击"启动"按钮启动对应模型
   - **停止模型**: 点击"停止"按钮停止运行中的模型
   - **查看状态**: 实时显示模型状态和待处理请求数
   - **模式识别**: 界面显示模型模式（💬 Chat, 📝 Base, 🔍 Embedding）

3. **日志查看**
   - 选择活动模型查看实时日志
   - 日志显示模型启动和运行过程
   - 支持自动刷新，显示最新200行

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

#### 模式路由说明
- **Chat模式**：仅支持 `/v1/chat/completions` 端点
- **Base模式**：仅支持 `/v1/completions` 端点  
- **Embedding模式**：仅支持 `/v1/embeddings` 端点

系统会根据模型模式自动验证请求端点的兼容性。

### 系统托盘操作

右键点击系统托盘图标：
- **打开WebUI**: 快速打开管理界面
- **重启Auto-Start模型**: 重启所有配置为自动启动的模型
- **卸载全部模型**: 停止所有运行中的模型
- **退出**: 关闭整个系统

## 🔧 高级配置

### 模型启动脚本

在 `Model_startup_script` 文件夹中为每个模型创建启动脚本（.bat文件）：

```batch
@echo off
chcp 65001 >nul
cd /d "%~dp0"
call conda activate your-model-env
python -m vllm.entrypoints.openai.api_server \
  --model /path/to/your/model \
  --port 10001 \
  --tensor-parallel-size 2 \
  --gpu-memory-utilization 0.9
```

### 多GPU环境配置

系统支持灵活的多GPU环境配置：
- **自动检测**: 启动时自动检测可用GPU
- **优先级适配**: 按配置文件中的优先级顺序选择最佳方案
- **动态切换**: GPU状态变化时自动重新配置
- **实时监控**: Web界面实时显示GPU状态和配置选择

### 日志管理

系统自动管理日志文件：
- 日志文件位置：`logs/` 目录
- 自动清理：保留最近9个日志文件
- 文件命名：`LLM-Manager_YYYYMMDDHHMMSS.log`

### 性能优化

1. **显存优化**
   - 准确配置 `gpu_mem_mb` 参数
   - 启用空闲模型自动卸载
   - 监控显存使用情况

2. **并发控制**
   - 使用全局加载锁确保模型顺序加载
   - 请求计数器追踪并发请求数
   - 智能资源分配和释放

3. **网络优化**
   - 调整API超时设置
   - 优化流式响应处理
   - 配置适当的并发连接数

## 🐛 故障排除

### 常见问题

1. **GPU检测失败**
   - 确保安装了NVIDIA驱动
   - 检查CUDA环境配置
   - 验证GPU是否被其他程序占用
   - 查看启动日志中的GPU检测结果
   - 确认GPU名称格式正确（去除"NVIDIA"、"GeForce"等前缀）

2. **模型启动失败**
   - 检查模型启动脚本路径和内容
   - 验证端口是否被占用
   - 查看模型日志获取详细错误信息
   - 确认显存配置是否准确
   - 检查优先级配置是否匹配当前GPU状态

3. **显存不足**
   - 停止不需要的模型
   - 调整模型显存配置
   - 考虑使用更小的模型
   - 检查显存配置是否与实际需求匹配
   - 利用优先级系统选择更适合的配置方案

4. **API请求失败**
   - 检查模型是否正常运行
   - 验证请求格式和参数
   - 查看API服务日志
   - 确认模型别名是否正确
   - 确认模型模式与API端点是否匹配

5. **优先级配置问题**
   - 确认配置文件格式正确
   - 检查required_gpus列表是否匹配实际GPU
   - 验证bat_path路径是否存在
   - 查看启动日志中的配置选择过程

### 调试模式

启用详细日志：
```python
# 在main.py中修改日志级别
logging.basicConfig(level=logging.DEBUG)
```

### 性能监控

使用Web界面监控：
- GPU使用率和显存状态
- 模型运行状态和请求数
- 实时日志输出

## 📊 系统架构

### 核心组件

```
LLM-Manager/
├── main.py              # 主程序入口，系统托盘管理
├── model_manager.py     # 模型管理核心，显存分配
├── api_server.py        # OpenAI兼容API服务
├── web_ui.py           # Gradio Web界面
├── gpu_utils.py        # GPU工具函数
├── config.json         # 配置文件
├── requirements.txt    # Python依赖
├── LLM-Manager.bat    # 启动脚本
└── Model_startup_script/ # 模型启动脚本
```

### 数据流

1. **请求流程**: API请求 → 模型识别 → 自动启动 → 代理转发
2. **管理流程**: Web界面 → 模型操作 → 状态更新 → 界面刷新
3. **监控流程**: GPU监控 → 资源检查 → 智能卸载 → 状态同步

## 🤝 项目声明

### 开发说明
- **个人项目**: 本项目完全为个人使用开发，未考虑通用性
- **不提供支持**: **不处理任何Issue、Pull Request或技术支持请求**
- **自行修改**: 使用者必须根据自身环境修改相关代码
- **仅供参考**: 代码结构和实现方式仅作为参考

### 使用建议
1. **仔细阅读**: 使用前请仔细阅读"本地化配置要求"章节
2. **逐步调试**: 建议逐步修改和测试每个配置项
3. **备份重要数据**: 修改前备份重要配置文件
4. **理解原理**: 建议理解代码原理后再进行修改

### 免责声明
本项目按"原样"提供，**不提供任何明示或暗示的保证**。使用者需自行承担使用风险，开发者不对任何损失或问题负责。

## 📄 许可证

本项目采用 [MIT许可证](LICENSE)。

## 📝 更新日志

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

#### 配置示例
```json
"RTX4060-V100": {
  "required_gpus": ["rtx 4060", "v100"],
  "bat_path": "Model_startup_script\\dual-gpu.bat",
  "gpu_mem_mb": {
    "rtx 4060": 6144,
    "v100": 16000
  }
},
"RTX4060": {
  "required_gpus": ["rtx 4060"],
  "bat_path": "Model_startup_script\\rtx4060-only.bat",
  "gpu_mem_mb": {
    "rtx 4060": 12288,
    "v100": 0
  }
}
```

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

#### 文档更新
- 添加embedding模型配置示例
- 添加embedding API使用示例
- 更新模式路由说明文档