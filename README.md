# LLM-Manager

**LLM-Manager** 是一个用于统一管理本地大型语言模型（LLM）的后端服务与监控工具。它通过统一 API 接口和动态资源调度，简化多模型在本地环境中的部署与调用流程。

> **⚠️ 重要说明**：  
> 本项目为个人开发工具，适用于本地实验环境。  
> 不包含任何模型文件。用户需自行准备模型启动脚本（如 `.bat` 或 `.sh`）。  
> 使用前需具备 Python 和本地 LLM 部署的基础能力。

---

## 核心功能

1. **统一 API 接口**  
   提供兼容 OpenAI 格式的标准接口：  
   - `/v1/completions`
   - `/v1/chat/completions`  
   - `/v1/embeddings`  
   - `/v1/rerank`
   - `/v1/models`
   请求自动路由至对应本地模型服务端口。

2. **插件化架构**  
   - **接口插件**：支持 `Chat`、`Base`、`Embedding`、`Reranker` 四种模型模式。  
   - **设备插件**：检测 `NVIDIA GPU`、`CPU`、`AMD 核显 780M`状态，用于动态调度。

3. **智能资源调度**  
   - **按需启动**：请求到达时自动启动模型，空闲超时后关闭以释放显存。  
   - **环境适配**：根据当前在线显卡型号自动选择匹配的启动参数。  
   - **并发控制**：优化高并发下的冷启动流程，避免线程阻塞。

4. **数据监控与计费**  
   - 使用 SQLite 记录请求日志。  
   - 支持两种计费模式：  
     - 按 Token 消耗（阶梯定价）  
     - 按使用时长（租赁场景）  
   - 提供 WebUI 实时展示吞吐量、成本统计与日志流。

5. **跨平台支持**  
   支持 Windows，Linux。

---

## 安装与启动

### 1. 环境要求
- Python 3.10+
- SQLite3（通常随系统或 Python 自动安装）
- Node.js 18+（仅用于前端构建；项目已包含预构建的 WebUI 文件）

### 2. 后端设置
```bash
# 克隆仓库
git clone https://github.com/lingyezhixing/LLM-Manager.git
cd LLM-Manager

# 安装依赖
pip install -r requirements.txt
```

> **配置文件**：  
> 项目不提供 `config.yaml` 示例。用户需**自行创建** `config.yaml` 文件于项目根目录，结构参考下文。

### 3. 启动服务
```bash
python main.py
```

启动后访问：`http://localhost:8080`

---

## 配置文件 (`config.yaml`)

请在项目根目录手动创建 `config.yaml` 文件。该文件为 YAML 格式，包含程序基础配置与模型定义。

### 程序基础配置
```yaml
program:
  host: "0.0.0.0"
  port: 8080
  log_level: "INFO"
  alive_time: 15          # 模型空闲超时时间（分钟），超时后自动关闭
  Disable_GPU_monitoring: false # 是否禁用 GPU 资源监控。禁用后，即使资源不足仍尝试启动模型。
```

### 模型配置 (`Local-Models`)

每个模型需定义唯一标识、运行模式、端口及启动脚本。支持多配置：优先使用靠前的配置，设备不满足时依次向下回退。

```yaml
Local-Models:
  Qwen-14B-Chat:
    aliases: ["gpt-3.5-turbo", "qwen-14b"]  # API 调用时使用的模型名称映射
    mode: "Chat"                            # 模式：Chat / Base / Embedding / Reranker
    port: 10001                             # 模型服务监听端口
    auto_start: false                       # 是否随服务启动

    Config1:
      required_devices: ["rtx 4060", "v100"] # 必须同时在线的设备
      script_path: "scripts/qwen_dual.bat"   # 启动脚本路径（Windows）或 .sh（Linux）
      memory_mb:
        "rtx 4060": 8000
        "v100": 16000

    Config2:
      required_devices: ["v100"]
      script_path: "scripts/qwen_single.bat"
      memory_mb:
        "v100": 24000
```

> ✅ **说明**：  
> - `script_path` 需指向用户自行编写的启动脚本，确保其可执行并正确绑定指定端口。  
> - `required_devices` 中的设备名称需与系统识别名称一致（如通过 `nvidia-smi` 查看）。  
> - 程序按顺序匹配 `Config1` 和 `Config2`，若设备不满足 `Config1`，则回退至 `Config2`。以此实现多 GPU 模型启动的灵活性。