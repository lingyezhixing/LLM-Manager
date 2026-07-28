#!/usr/bin/env bash
# Docker 入口:仅在退出码 81 上重启应用;其他码直接退出(交由 restart 策略或保持停止)。
# 注:生产建议外层套 tini(PID 1 僵尸回收 + 信号转发):ENTRYPOINT ["tini","--","/entrypoint.sh"]
set -u
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
rc=0
while :; do
  python -m llm_manager
  rc=$?
  [ "$rc" -eq 81 ] || break
  echo "LLM-Manager 请求重启(exit 81),重新启动..."
done
exit "$rc"
