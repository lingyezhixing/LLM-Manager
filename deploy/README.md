# LLM-Manager 部署 / 重启监督

程序配置变更后,WebUI(或 tray)的「重启」触发**优雅关闭 + 以退出码 `81` 退出**;部署侧的监督器在 `81` 上重启进程,新进程重读 DB 自动应用新参数。

## 81 契约

| 退出码 | 含义 | 监督器动作 |
|---|---|---|
| `81` | 请求重启(配置变更) | **重启** |
| `0`  | 正常退出(tray 退出 / Ctrl-C) | 不重启 |
| 其他 | 崩溃 | 不重启(首版不做崩溃自愈,可见失败优于静默循环) |

## 各平台

### Windows
`LLM-Manager.bat` 已内置 `if %ERRORLEVEL% EQU 81 goto run_loop`(必须 `EQU` 精确匹配)。经静默 VBS 包装照常工作。

### Linux
`./deploy/llm-manager.sh` 在 81 上循环。systemd:安装 `deploy/llm-manager.service`(`systemctl enable --now llm-manager`),`.sh` 内部处理 81,systemd `Restart=on-failure` 仅兜底脚本自身异常。

### Docker
`deploy/docker-entrypoint.sh` 在 81 上循环;`deploy/docker-compose.yml` 用 `restart: unless-stopped` 兜底。生产建议外层套 `tini`(PID 1 回收 + 信号)。**本仓库不构建镜像**(GPU/conda/卷属独立 DevOps 工程)——提供的是重启契约 + 入口模板。

## 验证清单
- 改重启字段(host/port/db_path/log_dir/log_level/claude_settings_path)→ WebUI「立即重启」→ 观察进程以 81 退出 → 监督器重启 → 前端自动重连刷新。
- tray「🔄 重启程序」走同一路径。

## 排错

- **Windows 改了配置却不重启**:确认 `.bat` 用的是 `if %ERRORLEVEL% EQU 81`(精确等于)。`if errorlevel 81` 是「≥ 81」,且 `errorlevel` 在某些路径下会读到上一次的残留值,不可靠。
- **重启后端口起不来 / 反复重启**:多半是旧进程残留没释放端口。Win:`netstat -ano | findstr :<port>` 找 PID → `taskkill /PID <pid> /F`;Linux:`lsof -i:<port>` / `fuser -k <port>/tcp`。多实例时注意端口别撞。
- **Docker 容器改配置后直接退出而非重启**:容器入口必须是 `docker-entrypoint.sh`(它在 81 上循环),不要把 `python -m llm_manager` 直接作入口(那样 81 退出 = 容器死亡,除非 compose `restart` 兜底重拉)。生产建议套 `tini` 作 PID 1 再跑入口脚本。
- **systemd 反复拉起 / 正常退出也重启**:用 `Restart=on-failure`(非 `always`)。`.sh` 内部已处理 81 循环,正常退出(0)不应被 systemd 重启;`always` 会把「tray 退出」也当成要重启。
- **前端卡在「正在重启」**:两阶段重连有 60s 硬超时(端口迟迟不释放 / 多实例占用时会触发)。超时后会显示错误条,点「刷新页面」手动恢复;若仍不通,按上一条排查端口。
- **退出码不是 81**:检查是否被外层 shell 包装吞掉了退出码(如 `python -m llm_manager || true`、VBS 包装的调用链)。监督器必须能拿到子进程的真实退出码。
