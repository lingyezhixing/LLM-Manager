# LLM-Manager

本地多 LLM 模型管理工具：按需启动 / 空闲回收本地模型进程（llama.cpp / lmdeploy / vLLM 等），对外暴露 OpenAI / Anthropic / Responses 兼容 API，记录用量与计费，并提供 WebUI 完成配置、模型、统计、日志管理。完全离线运行，无任何云端依赖（唯一联网点是系统页的「自更新」，且仅在你手动点击时联网）。

> 注意：
> - 这是个人开发工具，面向本地实验环境，请自行评估稳定性。
> - 不包含任何模型文件；模型启动命令在系统配置里填写。
> - 使用前需要 Python 基础，以及本地 LLM 部署的经验。

---

## 快速开始

### 环境要求

- Python 3.11+
- SQLite3（一般随 Python 自带）
- 建议使用 conda / venv 虚拟环境（Windows 下 `LLM-Manager.bat` 默认激活名为 `LLM-Manager` 的 conda 环境）

### 安装

```bash
git clone https://github.com/lingyezhixing/LLM-Manager.git
cd LLM-Manager
pip install -e .
# 可选能力（按需追加）:
#   [monitoring]  显卡监控
#   [tray]        系统托盘
#   [dev]         开发测试工具
```

### 启动

```bash
python -m llm_manager
# 打开 http://localhost:8080
```

Windows 可用 `LLM-Manager.bat` 静默后台启动。

首次启动会在 `data/` 下创建数据库，写入默认配置：监听 `0.0.0.0:8080`、空闲回收 60 分钟、日志保留 30 天 / 10 条。

### 添加第一个模型

1. 打开 WebUI → 系统 → 本地模型配置 → 添加模型。
2. 填写：
   - **名称**：`qwen2.5-7b`
   - **别名**：`qwen`（首个别名是对外服务名，客户端用它请求）
   - **模式**：`Chat`
   - **端口**：`8001`（模型进程实际监听的端口）
   - **启动方案**：可执行文件 + 参数
3. 保存。模型会在收到第一个请求时自动启动。

启动方案示例（llama.cpp）：

```
exe:  llama-server
args: -m E:/models/qwen2.5-7b-instruct.gguf --port {{port}}
```

- `{{port}}` 自动替换为上方填写的端口，`{{alias}}` 替换为首个别名；改端口 / 别名会同步传导到启动命令。
- 参数逐项填写，带引号的参数（如 JSON）无需手工转义。
- 可在「设备」里填 `required_devices`（如 `["rtx 4060"]`）与显存需求，按在线设备匹配启动方案；也可留空——留空则不按设备匹配，按在线即可启动。

### 调用 API

```bash
curl http://localhost:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"qwen","messages":[{"role":"user","content":"你好"}]}'
```

网关按 `model` 字段匹配别名，把请求转发到该模型对应的本地端口；模型未运行时会先自动启动。

## API 接口

| 接口 | 路径 |
|---|---|
| OpenAI | `/v1/chat/completions`、`/v1/completions`、`/v1/embeddings`、`/v1/rerank` |
| Anthropic | `/v1/messages` |
| OpenAI Responses | `/v1/responses` |
| 模型列表 | `/v1/models` |

Embedding / Reranker 模型同样走 OpenAI 路径。

## 配置

所有配置存放在 SQLite（`data/llm_manager.db`），通过 WebUI 修改，没有配置文件。

### 系统配置（系统 → 系统配置）

- 监听地址 / 端口：网关监听位置，改后需重启
- 空闲检测（分钟）：模型空闲多久后自动关闭释放显存
- 日志级别、日志保留（天数 / 条数）
- 页面顶部：当前版本、启动时间、运行时长、自更新入口

### 模型配置（系统 → 本地模型配置）

每个模型包含：

- 名称、别名（首个别名 = 对外服务名）、模式（Chat / Embedding / Reranker）、端口、自动启动
- 多个启动方案（scheme）：按在线设备依次匹配，第一个满足的生效，不满足自动回退下一个
- 计费：阶梯 token 计费或按小时租赁（可混合）

支持改名 + 历史数据迁移（可选把旧用量 / 日志迁移到新名字，或删除旧日志）；删除模型定义会连带清理其日志（保留请求记录）。

### 数据库管理（系统 → 数据库管理）

查看各模型的数据量、删除模型数据（级联 + 空间回收）、清理孤立模型。

### 环境变量

启动时覆写并持久化到配置：

- `LLM_MANAGER_HOST` / `LLM_MANAGER_PORT` / `LLM_MANAGER_ALIVE_TIME` / `LLM_MANAGER_LOG_LEVEL`

数据库文件位置（不写入配置，仅决定 DB 路径）：`LLM_MANAGER_DB_PATH`

### 重启规则

改 host / port / log_level 等字段需要重启生效；WebUI 顶部会自动提示并一键重启。程序内置监督器拉起全新进程，正在服务的模型会中断，重启后自动恢复。

## 设备监控

监控 NVIDIA / AMD / Intel 三家的独显与核显，Windows / Linux 双平台：

- NVIDIA：nvidia-smi（双平台）
- AMD：Linux 走 amdgpu sysfs，Windows 走 LibreHardwareMonitor
- Intel：Linux 走 i915 + intel_gpu_top，Windows 走 LibreHardwareMonitor
- CPU：温度 / 频率 / 内存占用

> 以上为理论覆盖，尚未在全部硬件组合上实测；个别指标读不到会显示为空，不影响设备匹配与启动。

设备名在配置里按原样填写，匹配时自动归一化。

## 日志与数据

- 系统日志与模型日志都写入数据库，WebUI「日志查看」页实时查看、全文检索、按时间 / 条数自动清理
- 模型日志可在「模型管理」页内联查看
- 进程崩溃后残留会话自动收口为已结束（30s 心跳）

## 自更新

- 版本即 git 标签；更新目标两档：**稳定版**（最近发布标签）或 **最新提交**
- 仅向前更新，不支持回退；本地未提交改动不预拒，仅在冲突时拒绝（不会覆盖）
- 程序启动时自动检查一次，此后只有你点「检查更新」才联网
- 需要以 git 克隆方式部署（非 git 目录自动隐藏更新功能）；Docker 下宿主仓库须为 root 属主

## 系统托盘

- 打开 WebUI、网络唤醒、Claude Code 预设一键切换、快速启停模型、优雅退出
- 无桌面环境自动退化为静默后台运行

## 工具箱（网络唤醒 / Claude Code 预设）

- 网络唤醒：配置广播地址 + MAC，可对远程设备（如 NAS）发送魔术包唤醒
- Claude Code 预设：在多个 Claude 配置之间切换（写入 settings.json），与托盘菜单互通

## Docker 部署

```bash
cp Dockerfile.example Dockerfile
cp docker-compose.yml.example docker-compose.yml
docker compose up -d --build   # 首次构建 + 启动
docker compose up -d           # 日常：改代码 / 配置后重启即可
```

- 整个代码库挂载进容器，改代码 / 前端即时生效，只有依赖变更需重建
- `data/`、`logs/` 落在宿主机，天然持久化
- 镜像预装 llama.cpp 编译 / 运行所需的系统库（Vulkan / OpenBLAS / cmake）
- Intel iGPU 监控需容器 `SYS_ADMIN` + `seccomp=unconfined`（模板已含）

## 升级注意

- v3.x 之间：如无特别说明，任意 v3.x 均可无感升级到任意更高的 v3.x（不保证降级）
- v2.x 用户：v2 的配置（config.yaml）与计费数据不会迁移，需在网页重新录入；v2 数据库请备份后删库重建。详见 v3.0.0 发布说明
- 升级通过「自更新」或 `git pull` 完成

## 架构

```
config → state → supervisor → runtime → data → gateway → tray
```

单进程 + 单事件循环；配置单一源（DB）；内置 parent+worker 自重启（退出码 81 契约）。详细分层、不变量与约定见 `AGENTS.md`。

## 开发

后端（项目根，开发用 conda env `LLM-Manager-Dev`）：

```bash
python -m pytest tests -q
ruff format --check .
ruff check .
pyright src/llm_manager
```

前端（`frontend/`）：

```bash
npm run build        # tsc -b && vite build；8080 serve 的是 dist 构建产物，改前端后必跑
npx oxlint src
npx tsc -b
```