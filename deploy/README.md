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
