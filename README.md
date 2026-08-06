# LLM-Manager v3.0.0a1

**LLM-Manager** 是一个统一管理本地大型语言模型（LLM）的代理网关 + WebUI：按需启动 / 空闲回收本地模型进程（llama.cpp / lmdeploy / vLLM …），对外暴露 OpenAI / Anthropic / Responses 兼容 API，记录用量与计费，并提供系统配置、模型管理、用量统计、日志查看的完整前端。**完全离线运行**（无任何云端依赖）。

> **⚠️ 重要说明**：
> 本项目为个人开发工具，适用于本地实验环境。
> 不包含任何模型文件。模型启动命令在系统配置中定义（结构化命令，无需准备启动脚本）。
> 使用前需具备 Python 和本地 LLM 部署的基础能力。

---

## 功能特性

### 1. 统一 API 接口
提供 OpenAI / Anthropic / Responses 三种兼容格式，请求按 `model` 字段解析别名并自动路由至对应本地模型服务端口：
- `/v1/chat/completions`、`/v1/completions`（OpenAI）
- `/v1/embeddings`、`/v1/rerank`（Embedding / Reranker）
- `/v1/messages`（Anthropic Claude API）
- `/v1/responses`（OpenAI Responses API）
- `/v1/models`

### 2. 按需启动与智能调度
- **按需启动**：请求到达时自动启动模型，空闲超时后自动关闭以释放显存。
- **环境适配**：根据当前在线显卡型号自动选择匹配的启动方案（scheme），设备不满足时自动回退到下一个方案。
- **并发安全**：单派发 Future 去重 + 全局 spawn 锁 + owner-token guard，高并发冷启动不串槽。
- **健康探测**：纯函数 `probe_registry` 按模型模式（Chat / Embedding / Reranker）分派探测方式；设备监控覆盖 NVIDIA GPU（nvidia-smi）、Linux Intel iGPU（i915 sysfs）与 AMD 780M 核显（LibreHardwareMonitor，Windows）。

### 3. 全量 Token 追踪
- 按请求路径自动分派解析器（OpenAI / Anthropic / Responses 三种格式），流量自动纳入统计，无需白名单配置。
- 适配 **llama.cpp** 与 **lmdeploy** 双后端（流式请求自动注入 `include_usage`，保障流式用量完整）。

### 4. 计费与用量统计
- **计费系统**：阶梯 token 计费 + 按时租赁，混合计费汇总（分级按量 / 按时计费）。
- **分析看板**：成本趋势 / Token 趋势 / 单模型统计 / 使用量汇总（WebUI 用量统计页，实时 SSE 刷新）。

### 5. 系统托盘
- 🌐 一键打开 WebUI · 🔔 网络唤醒远程设备（如飞牛 NAS）· Claude API 预设一键切换（子菜单显示当前配置）· ▶ 重启自启模型 / ⏹ 卸载全部模型 · ❌ 优雅退出。
- 无头环境（无桌面 / 无 pystray）自动降级为静默后台运行。

### 6. 数据与日志管理
- **日志全落库**（SQLite）：系统日志会话 + 模型日志，WebUI 双 Tab 日志页（会话列表 + 实时行详情，SSE 流式）。
- **保留规则**：按时间 / 按条数自动清理，可配置。
- **数据管理**：删除模型数据（级联 + VACUUM 回收）、孤立模型检测、存储统计。

### 7. WebUI 前端
- React 19 + Vite + TypeScript + Tailwind v4 + TanStack Query，双主题（深 / 亮），实时监控。
- 页面：**概览**（设备 / 模型 / 会话实时状态）· **模型管理**（启停 + 实时日志 + 定义 CRUD）· **用量统计** · **日志查看** · **系统配置**（程序 / 模型 / 计费 / WOL / Claude 预设 / 日志保留 / 数据管理）。
- 配置修改即时生效或提示重启（需重启字段自动检测 + 一键自重启，退出码 81 契约）。

---

## 架构

```
config   ── 纯数据 + 校验（DB → frozen dataclasses）
state    ── 内存状态机 + 单派发 inflight Future
supervisor ── 子进程管理（kill_tree + 单 wait 协程）
runtime  ── 生命周期编排 / 纯函数资源调度 / 心跳 / 日志保留
data     ── SQLite 持久化 + 日志 / 用量 / 配置存储
gateway  ── 流式代理 + REST/SSE 端点 + 别名解析
tray     ── 系统托盘（WOL / Claude 预设 / 快速启停）
```

- **单进程模型**：一个 Python 进程跑一个 app（FastAPI + uvicorn），模块级单例内存状态，SQLite 单连接 + `write_lock` 串行化。
- **配置单一源**：全部配置存 SQLite（`data/llm_manager.db`），运行时只读 frozen 快照；环境变量仅在启动期覆写并持久化。
- **自重启**：程序内置 parent 监督器（`python -m llm_manager`）spawn 并管理 worker；配置变更重启时 worker 以退出码 81 退出，parent 拉起全新 worker（每次全新进程，构造性干净）。`LLM-Manager.bat` 仅作 Windows 静默后台启动，不参与重启。

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

启动后访问：`http://localhost:8080`（8080 端口同时 serve API 与前端构建产物 `frontend/dist`）

### 4. Docker 部署（挂载模式）
```bash
# 从 example 模板复制出实际文件（实际文件已 gitignore，可随意修改，不影响 git 更新）
cp Dockerfile.example Dockerfile
cp docker-compose.yml.example docker-compose.yml

docker compose up -d --build     # 首次构建 + 启动
docker compose up -d             # 日常：改代码/配置后重启即可，无需重建
```
- 整个代码库挂载进容器（`.:/app`）：环境是环境、代码是代码，改代码/前端 dist 即时生效，只有依赖变更才需重建
- `data/`、`logs/` 落在宿主机，天然持久化；配置全部走 WebUI（DB 化），无需进容器改文件
- 容器内 `python -m llm_manager` 即 parent+worker 自重启；镜像预装 llama.cpp 编译/运行所需系统库（Vulkan/OpenBLAS/cmake 工具链），llama.cpp 编译脚本不归本仓库管理
- Intel iGPU 监控需容器 `SYS_ADMIN` + `seccomp=unconfined`（compose example 已含，仅供 i915 PMU 监控）

---

## 系统配置（SQLite DB 化）

全部配置存储在 SQLite 数据库（默认 `data/llm_manager.db`）中，通过 WebUI「系统配置」页或 `/api/config/*` 修改，无需编辑配置文件：

- **程序配置**：监听地址 / 端口、日志级别、空闲回收间隔（分钟）、Claude settings 路径、WOL、Claude 预设、日志保留规则。
- **模型定义**：名称、别名、模式、端口、自动启动、设备方案（scheme: required_devices / memory_mb / **结构化启动命令** exe + args + env + conda_env）、计费（阶梯 / 按时）。
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

---

## 开发

后端（项目根，conda env `LLM-Manager`）：
```bash
python -m pytest tests -q     # 全量测试（含 smoke）
ruff check .                  # lint
pyright src/llm_manager       # 类型检查
```

前端（`frontend/`）：
```bash
npm run build        # = tsc -b && vite build；8080 端口 serve 的是 dist 构建产物，改前端后必跑
npx oxlint src       # lint
```
