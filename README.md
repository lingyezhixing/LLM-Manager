这是一个为您重写的 README.md。我已去除了所有营销性质的夸大词汇，专注于功能描述和实际配置说明，并根据提供的 git log 整理了详细且合理的版本更新日志。

***

# LLM-Manager

**LLM-Manager** 是一个用于统一管理本地大型语言模型（LLM）的后端服务与监控工具。它旨在解决本地多模型部署时的端口管理、资源调度和统一接口调用问题。

> **⚠️ 重要说明**：
> 这是一个个人开发的工具，主要用于个人或小团队的本地实验环境。
> 项目不包含任何模型文件，需要用户自行准备模型启动脚本（如 `.bat` 或 `.sh`）。
> 使用前需要具备一定的 Python 和本地 LLM 部署基础。

---

## 核心功能

1.  **统一 API 接口**：提供兼容 OpenAI 格式的统一入口 (`/v1/chat/completions`, `/v1/embeddings` 等)，后端自动路由到对应的本地端口。
2.  **插件化架构**：
    *   **接口插件**：支持 Chat、Base、Embedding、Reranker 四种模型模式。
    *   **设备插件**：支持 CPU、NVIDIA GPU (RTX系列/数据中心卡) 的状态检测。
3.  **智能资源调度**：
    *   **按需启动**：请求到达时自动启动模型，空闲超时自动关闭释放显存。
    *   **动态配置**：根据当前显卡在线状态（如 4060 vs V100），自动选择适配的启动参数。
    *   **并发控制**：优化了高并发下的冷启动处理，防止请求死锁。
4.  **数据监控与计费**：
    *   基于 SQLite 的请求日志记录。
    *   支持**按 Token 计费**（阶梯定价）和**按时计费**（租赁场景）两种模式。
    *   提供基于 WebUI 的实时吞吐量、成本统计和日志流监控。
5.  **跨平台支持**：支持 Windows 和 Linux 环境（Linux 适配进行中）。

---

## 安装与启动

### 1. 环境要求
*   Python 3.10+
*   Node.js 18+ (用于构建前端，项目已经包含构建好的前端文件，如需自行构建请安装)
*   SQLite3

### 2. 后端设置
```bash
# 克隆仓库
git clone https://github.com/your-repo/LLM-Manager.git
cd LLM-Manager

# 安装依赖
pip install -r requirements.txt

# (可选) 修改配置文件
# copy config.example.yaml config.yaml
```

### 3. 前端构建
WebUI 源码位于 `webui` 目录，基于 React + Vite。
```bash
cd webui
npm install
npm run build
# 构建产物将生成在 webui/dist 目录，后端会自动挂载
```

### 4. 启动服务
回到项目根目录：
```bash
python main.py
```
启动后，访问 `http://localhost:8080` 即可进入管理后台。

---

## 配置指南 (`config.yaml`)

本项目已从 JSON 配置迁移至 YAML 格式，config.yaml请放置在项目根目录。以下是核心配置说明：

### 程序基础配置
```yaml
program:
  host: "0.0.0.0"
  port: 8080
  log_level: "INFO"
  alive_time: 15          # 模型空闲自动关闭时间（分钟）
  Disable_GPU_monitoring: false # 是否禁用设备资源监控，禁用状态下资源不足时依然会强制启动模型
```

### 模型配置 (Local-Models)
需要在 `config.yaml` 中手动定义每个模型的启动参数。

```yaml
Local-Models:
  # 模型唯一标识符
  Qwen-14B-Chat:
    aliases: ["gpt-3.5-turbo", "qwen-14b"]  # API 调用的模型名称映射
    mode: "Chat"                            # 模式: Chat, Base, Embedding, Reranker
    port: 10001                             # 目标服务实际端口
    auto_start: false                       # 是否随程序启动

    # 配置方案 A: 双卡模式 (优先级高)
    Dual-GPU-Config:
      required_devices: ["rtx 4060", "v100"] # 必须同时在线的设备
      script_path: "scripts/qwen_dual.bat"   # 启动脚本路径
      memory_mb:
        "rtx 4060": 8000
        "v100": 16000

    # 配置方案 B: 单卡模式 (优先级低，当方案A设备不满足时回退)
    Single-GPU-Config:
      required_devices: ["v100"]
      script_path: "scripts/qwen_single.bat"
      memory_mb:
        "v100": 24000
```

---

## 更新日志 (Changelog)

### v2.1.2 - 2025-12-03 (Current)
**稳定性修复**
*   **[Critical]** 修复了高并发请求触发模型冷启动时，导致线程池耗尽（Thread Pool Starvation）从而引发系统假死的严重 Bug。
*   在 Router 层引入本地异步锁机制，确保同一模型启动过程只占用单线程，其余请求异步等待。
*   优化了配置文件加载逻辑。

### v2.1.0 - 2025-11-23
**架构与配置升级**
*   **配置迁移**：配置文件格式从 `.json` 全面迁移至 `.yaml`，提高了可读性和配置灵活性。
*   **Linux 适配**：初步实现 Linux 环境支持，统一了路径处理逻辑 (`script_path` 替代 `bat_path`)。
*   **进程管理**：修复了进程管理器在无进程关闭时超时的 Bug，优化了关闭速度。
*   **日志系统**：重构日志模块，解决了重复实例和初始化顺序导致的日志丢失问题。

### v2.0.0 - 2025-10-26
**WebUI 重构与计费系统**
*   **WebUI**：完全重写前端界面，集成实时数据监控面板。
*   **计费系统**：
    *   新增按时间计费模式（适用于算力租赁场景）。
    *   完善按 Token 阶梯计费逻辑。
    *   实现了完整的数据管理和账单设置页面。
*   **性能优化**：后端数据计算全面向量化（Vectorized），大幅降低统计接口延迟。

### v1.1.0 - 2025-10-07
**并发与死锁修复**
*   修复了模型启动/停止时的死锁问题，引入带超时的锁机制。
*   支持中断正在启动中的模型。
*   优化了自动重启（Auto-start）逻辑。

### v1.0.0 - 2025-09-22
**架构重构 (里程碑)**
*   **插件化架构**：将核心功能解耦，实现了设备插件和接口插件的动态加载。
*   **模块化**：分离了 `ProcessManager`、`ConfigManager` 和 `Monitor` 模块。
*   **异步化**：数据库操作和设备信息获取改为异步执行，防止阻塞 API 线程。
*   **新增功能**：
    *   Token 消耗记录与追踪。
    *   模型运行时间监控。
    *   系统托盘服务支持（WebUI 快捷入口）。

### v0.x.x - 早期版本 (2025-09)
*   **v0.5.0**: 新增 Reranker 模型模式支持 (`/v1/rerank`)。
*   **v0.4.0**: 实现动态 GPU 优先级配置系统，支持多环境自适应。
*   **v0.3.0**: 新增 Embedding 模型模式支持。
*   **v0.1.0**: 项目初始化，基本的进程管理和 API 转发功能。