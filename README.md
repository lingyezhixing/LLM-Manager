# 🧠 LLM-Manager

LLM-Manager 是一个功能强大的大型语言模型管理工具，旨在帮助用户高效地管理和部署多个LLM模型。该系统提供了统一的Web界面、API接口和智能资源管理功能，支持自动模型加载、显存优化和实时监控。

## 🌟 核心特性

### 🔧 模型管理
- **多模型支持**: 同时管理多个LLM模型，包括Qwen、GLM、Sakura等系列
- **智能别名系统**: 支持模型别名，灵活识别不同名称的模型
- **自动启动**: 配置模型自动启动，系统启动时按需加载
- **健康检查**: 自动检测模型状态，确保服务可用性

### 💾 智能资源管理
- **GPU显存优化**: 自动监控和管理GPU显存使用
- **动态卸载**: 空闲模型自动卸载，释放资源
- **显存不足处理**: 智能停止空闲模型为新模型腾出空间
- **多GPU支持**: 支持多GPU环境下的资源分配

### 🌐 统一接口
- **OpenAI兼容API**: 提供与OpenAI API兼容的统一接口
- **流式响应支持**: 支持流式和非流式响应
- **自动模型加载**: 请求时自动启动对应模型
- **请求追踪**: 实时追踪模型请求状态和数量

### 🖥️ 可视化管理
- **实时GPU监控**: 显示GPU使用率和显存状态
- **模型控制面板**: 可视化启动/停止模型
- **实时日志查看**: 查看模型启动和运行日志
- **系统托盘**: Windows系统托盘快捷操作

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

4. **配置模型**
   编辑 `config.json` 文件，配置您的模型信息（详见配置说明）

5. **启动系统**
   ```bash
   # 方式1：直接运行Python脚本
   python main.py
   
   # 方式2：使用批处理文件（推荐）
   LLM-Manager.bat
   ```

### 启动验证

系统启动后会：
1. 检测GPU环境（需要同时存在RTX 4060和V100）
2. 启动Web管理界面（默认：http://127.0.0.1:10000）
3. 启动API服务（默认：http://0.0.0.0:8080）
4. 在系统托盘显示管理图标

## ⚙️ 配置说明

### 主配置文件结构

`config.json` 包含两个主要部分：

```json
{
  "program": {
    "openai_host": "0.0.0.0",
    "openai_port": 8080,
    "webui_host": "127.0.0.1", 
    "webui_port": 10000,
    "Disable_GPU_monitoring": false,
    "alive_time": 60
  },
  "ModelName": {
    "aliases": ["primary-alias", "alias1", "alias2"],
    "bat_path": "Model_startup_script\\model_name.bat",
    "mode": "Chat",
    "gpu_mem_mb": {
      "rtx 4060": 6144,
      "v100": 16000
    },
    "port": 10001,
    "auto_start": false
  }
}
```

### 程序配置参数

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `openai_host` | API服务监听地址 | "0.0.0.0" |
| `openai_port` | API服务端口 | 8080 |
| `webui_host` | Web界面监听地址 | "127.0.0.1" |
| `webui_port` | Web界面端口 | 10000 |
| `Disable_GPU_monitoring` | 禁用GPU监控 | false |
| `alive_time` | 模型空闲超时时间（分钟） | 60 |

### 模型配置参数

| 参数 | 说明 | 必需 |
|------|------|------|
| `aliases` | 模型别名列表，第一个为主名称 | ✅ |
| `bat_path` | 模型启动脚本路径 | ✅ |
| `mode` | 模式（"Chat"或"Base"） | ✅ |
| `gpu_mem_mb` | GPU显存需求配置 | ✅ |
| `port` | 模型服务端口 | ✅ |
| `auto_start` | 是否自动启动 | ❌ |

### GPU显存配置

`gpu_mem_mb` 配置示例：
```json
"gpu_mem_mb": {
  "rtx 4060": 6144,    // RTX 4060需要6GB显存
  "v100": 16000        // V100需要16GB显存
}
```

**注意**：
- 设置为0表示该GPU不分配显存
- GPU名称使用简化格式（去除"NVIDIA"、"GeForce"等前缀）
- 支持多GPU同时分配

## 📖 使用指南

### Web界面操作

1. **访问管理界面**
   - 打开浏览器访问：`http://127.0.0.1:10000`
   - 界面显示GPU监控和模型控制面板

2. **模型管理**
   - **启动模型**: 点击"启动"按钮启动对应模型
   - **停止模型**: 点击"停止"按钮停止运行中的模型
   - **查看状态**: 实时显示模型状态和待处理请求数

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

系统要求同时存在特定GPU（如RTX 4060和V100），可在 `main.py:116` 修改：

```python
required_gpus = {"rtx 4060", "v100"}  # 修改为您需要的GPU组合
```

### 日志管理

系统自动管理日志文件：
- 日志文件位置：`logs/` 目录
- 自动清理：保留最近9个日志文件
- 文件命名：`LLM-Manager_YYYYMMDDHHMMSS.log`

### 性能优化

1. **显存优化**
   - 合理设置 `gpu_mem_mb` 参数
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

2. **模型启动失败**
   - 检查模型启动脚本路径
   - 验证端口是否被占用
   - 查看模型日志获取详细错误信息

3. **显存不足**
   - 停止不需要的模型
   - 调整模型显存配置
   - 考虑使用更小的模型

4. **API请求失败**
   - 检查模型是否正常运行
   - 验证请求格式和参数
   - 查看API服务日志

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
├── main.py              # 主程序入口
├── model_manager.py     # 模型管理核心
├── api_server.py        # API服务层
├── web_ui.py           # Web界面
├── gpu_utils.py        # GPU工具函数
├── config.json         # 配置文件
└── Model_startup_script/ # 模型启动脚本
```

### 数据流

1. **请求流程**: API请求 → 模型识别 → 自动启动 → 代理转发
2. **管理流程**: Web界面 → 模型操作 → 状态更新 → 界面刷新
3. **监控流程**: GPU监控 → 资源检查 → 智能卸载 → 状态同步

## 🤝 贡献指南

欢迎提交Issue和Pull Request！

1. Fork 项目
2. 创建功能分支
3. 提交更改
4. 推送到分支
5. 创建Pull Request

## 📄 许可证

本项目采用 [MIT许可证](LICENSE)。

## 📞 支持与反馈

如有问题或建议，请通过以下方式联系：
- 提交Issue
- 发送邮件至项目维护者
- 查看项目文档和Wiki

---

**注意**: 本系统为内部工具，请根据实际需求和环境配置进行调整。在使用前请仔细阅读配置说明和故障排除指南。