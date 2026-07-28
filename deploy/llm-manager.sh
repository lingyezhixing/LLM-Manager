#!/usr/bin/env bash
# LLM-Manager 入口(带重启监督)。应用以退出码 81 表示"请求重启";
# 仅在 81 上重跑;其他退出码(0=正常退出 / 崩溃)都不重启。
# 注:conda 激活方式按你的安装调整(可能需先 source <conda>/etc/profile.d/conda.sh)。
set -u
cd "$(dirname "$0")/.."            # 回到仓库根(deploy/ 在根下一层)
conda activate LLM-Manager 2>/dev/null || true
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
while :; do
  python -m llm_manager
  rc=$?
  if [ "$rc" -ne 81 ]; then
    echo "LLM-Manager 退出(码 $rc),不重启。"
    exit "$rc"
  fi
  echo "LLM-Manager 请求重启(exit 81),重新启动..."
done
