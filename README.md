# LLM-Manager

**LLM-Manager** 是一个统一管理本地大型语言模型（LLM）的后端网关。通过统一 API 接口和动态资源调度，简化多模型在本地环境中的部署与调用流程。

> **⚠️ 重要说明**：
> 本项目为个人开发工具，适用于本地实验环境。
> 不包含任何模型文件。模型启动命令在系统配置中定义（结构化命令，无需准备启动脚本）。
> 使用前需具备 Python 和本地 LLM 部署的基础能力。

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

6. **计费与用量统计**
   - **计费系统**：阶梯 token 计费 + 按时租赁，混合计费汇总。
   - **分析看板**：成本趋势 / Token 趋势 / 单模型统计 / 使用量汇总（WebUI 用量统计页）。

7. **数据管理与日志**
   - **数据管理**：删除模型数据（级联 + VACUUM 回收）、孤立模型检测、存储统计。
   - **日志查看**：系统 / 模型日志全部落库（SQLite），WebUI 双 Tab 日志页（会话列表 + 实时行详情），保留规则自动清理（按时间 / 按条数）。

8. **WebUI 前端**
   - React + TypeScript 实时监控界面：概览、模型管理（启停 + 实时日志）、用量统计、日志查看、系统配置（模型定义 CRUD / 计费 / WOL / Claude 预设 / 日志保留）、数据库管理。
   - 配置修改即时生效或提示重启（需重启字段自动检测 + 一键自重启，退出码 81 契约）。

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

### 3. 启动服务
```bash
python -m llm_manager
```

启动后访问：`http://localhost:8080`

---

## 系统配置（SQLite DB 化）

全部配置存储在 SQLite 数据库（默认 `data/llm_manager.db`）中，通过 WebUI「系统配置」页或 `/api/config/*` 修改，无需编辑配置文件：

- **程序配置**：监听地址 / 端口、日志级别、空闲回收间隔（分钟）、Claude settings 路径、WOL、Claude 预设、日志保留规则。
- **模型定义**：名称、别名、模式、端口、自动启动、设备方案（scheme: required_devices / memory_mb / **结构化启动命令** exe + args + env）、计费（阶梯 / 按时）。
- **环境变量**（可选，启动时覆写并持久化）：`LLM_MANAGER_HOST` / `LLM_MANAGER_PORT` / `LLM_MANAGER_ALIVE_TIME` / `LLM_MANAGER_LOG_LEVEL` / `LLM_MANAGER_DB_PATH`。

首次启动（空库）时，若项目根目录存在 `config.yaml`（旧版 YAML 配置，结构参考下方），会作为一次性引导导入；否则使用默认配置。导入后配置以 DB 为准，`config.yaml` 不再被读取。

### 旧版 YAML 导入格式（可选）

```yaml
program:
  host: "0.0.0.0"
  port: 8080
  log_level: "INFO"
  alive_time: 60          # 模型空闲超时时间（分钟），超时后自动关闭
  claude_settings_path: "C:\\Users\\<you>\\.claude\\settings.json"  # 可选：托盘 Claude 配置切换的目标文件

Local-Models:
  Qwen-14B-Chat:
    aliases: ["gpt-3.5-turbo", "qwen-14b"]  # aliases[0]=主别名=下游 served name
    mode: "Chat"                            # Chat / Embedding / Reranker
    port: 10001
    auto_start: false

    RTX4060:                                # scheme 名 = config_source
      required_devices: ["rtx 4060"]        # 需与系统识别名称一致（nvidia-smi）
      memory_mb: {"rtx 4060": 8000}
      command:                              # 结构化启动命令（替代旧版 script_path）
        exe: "lmdeploy"
        args: ["serve", "api_server", "E:/models/Qwen-14B", "--server-port", "10001"]
        env: {"CUDA_VISIBLE_DEVICES": "0"}
        conda_env: "lmdeploy"               # 可选：conda 环境
```

> ✅ **说明**：设备不满足前一个 scheme 时自动回退到下一个（多 GPU 启动灵活性）。
