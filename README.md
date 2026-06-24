# LLM-Manager

**LLM-Manager** 是一个统一管理本地大型语言模型（LLM）的后端网关。通过统一 API 接口和动态资源调度，简化多模型在本地环境中的部署与调用流程。

> **⚠️ 重要说明**：
> 本项目为个人开发工具，适用于本地实验环境。
> 不包含任何模型文件。用户需自行准备模型启动脚本（如 `.bat` 或 `.sh`）。
> 使用前需具备 Python 和本地 LLM 部署的基础能力。

> **🚧 重构进行中（未发布，请勿直接 clone 使用）**：
> 后端正从旧的「插件化 `core/` + React WebUI」架构迁移到函数式轻量框架 [`src/llm_manager/`](src/llm_manager/)。
> - **已迁移**：统一 API 代理转发、多端点 Token 追踪、模型生命周期与调度、NVIDIA + AMD 780M 设备监控、系统托盘 / 网络唤醒 / Claude 配置切换。
> - **待迁移**：计费系统（阶梯 / 按时租赁）、分析看板、数据管理（删模型 + VACUUM）、运行时间追踪、WebUI 前端、CPU 监控。
> - 旧代码冻结在 [`_legacy/`](_legacy/)，仅供历史参考，新框架不引用。

---

## 核心功能（已迁移）

1. **统一 API 接口**
   提供兼容 OpenAI 格式的标准接口，同时支持 Anthropic 与 Responses API：
   - `/v1/chat/completions`（OpenAI Chat）
   - `/v1/completions`（OpenAI Completions）
   - `/v1/embeddings`（OpenAI Embedding）
   - `/v1/rerank`（Reranker）
   - `/v1/messages`（Anthropic Claude API）
   - `/v1/responses`（OpenAI Responses API）
   - `/v1/models`
   请求按 `model` 字段解析别名并自动路由至对应本地模型服务端口。

2. **函数式探测器架构**
   - **健康探测器**：以纯函数 `probe_registry`（按模型模式分派）替代接口插件，支持 `Chat`、`Base`、`Embedding`、`Reranker` 四种模式的健康检查。
   - **设备监控**：检测 `NVIDIA GPU`（nvidia-smi）与 `AMD 780M` 核显（LibreHardwareMonitor）状态，用于动态调度。

3. **智能资源调度**
   - **按需启动**：请求到达时自动启动模型，空闲超时后关闭以释放显存。
   - **环境适配**：根据当前在线显卡型号自动选择匹配的启动配置（scheme）。
   - **并发控制**：单派发 Future 去重 + 全局 spawn 锁 + owner-token guard，优化高并发冷启动流程，避免线程阻塞与跨代状态串槽。

4. **多端点 Token 追踪**
   - 按请求路径自动分派到对应解析器（OpenAI / Anthropic / Responses 三种格式）。
   - 适配 `llama.cpp` 与 `lmdeploy` 双后端（流式请求自动注入 `include_usage`）。
   - 全量追踪（track-all）：所有模型的流量自动纳入统计，无需手动配置白名单。

5. **系统托盘增强**
   - **网络唤醒（WOL）**：托盘菜单支持唤醒远程设备（如飞牛 NAS）。
   - **Claude 配置切换**：在预设的 Claude API 配置间一键切换（如 GLM / Local），子菜单显示当前配置。

---

## 规划中（待从旧架构迁移）

以下能力仍存在于 [`_legacy/`](_legacy/)，尚未迁移到新框架：

- **计费系统**：阶梯 token 计费 + 按时租赁，混合计费汇总（`_legacy/core/data_manager.py`）。
- **分析看板 API**：成本趋势 / Token 趋势 / 单模型统计 / 使用量汇总。
- **数据管理**：删除模型 + VACUUM 回收、孤立模型检测、存储统计。
- **运行时间追踪**：程序 / 模型运行时间记录与心跳。
- **WebUI 前端**：React + TypeScript 实时监控界面（旧版在 [`_legacy/webui/`](_legacy/webui/)）。
- **CPU/RAM 监控**：CPU 占用、内存、温度（无 Admin 权限时降级读核显温度）。

---

## 安装与启动

### 1. 环境要求
- Python 3.11+
- SQLite3（通常随系统或 Python 自动安装）
- conda 或 venv 虚拟环境（[`LLM-Manager.bat`](LLM-Manager.bat) 默认激活名为 `LLM-Manager` 的 conda 环境）

### 2. 安装
```bash
# 克隆仓库
git clone https://github.com/lingyezhixing/LLM-Manager.git
cd LLM-Manager

# 安装依赖：核心 + AMD 780M 监控[monitoring] + 系统托盘[tray] + 开发测试[dev]
pip install -e ".[monitoring,tray,dev]"
# 仅运行（不含开发工具）：pip install -e ".[monitoring,tray]"
```

> **配置文件**：
> 项目不提供 `config.yaml` 示例。用户需**自行创建** `config.yaml` 文件于项目根目录，结构参考下文。

### 3. 启动服务
```bash
python -m llm_manager
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
  alive_time: 60          # 模型空闲超时时间（分钟），超时后自动关闭
  claude_settings_path: "C:\\Users\\<you>\\.claude\\settings.json"  # 可选：托盘 Claude 配置切换的目标文件
```

### 模型配置 (`Local-Models`)

每个模型需定义唯一标识、运行模式、端口及启动脚本。支持多配置（scheme）：优先使用靠前的配置，设备不满足时依次向下回退。

```yaml
Local-Models:
  Qwen-14B-Chat:
    aliases: ["gpt-3.5-turbo", "qwen-14b"]  # API 调用时使用的模型名称映射；aliases[0]=主别名=下游 served name
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
