"""LLM-Manager backend (lightweight redesign)."""

# 退出码协议:请求重启(不变量 5)。包级常量——restart 端点(config_api)与
# parent/worker 监督(runner)都依赖它,置于包根避免任一方向的分层倒挂。
RESTART_EXIT_CODE = 81
