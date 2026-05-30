#!/usr/bin/env bash
# 供 cron / systemd 调用的包装脚本：固定工作目录后执行机器人。
# 用法：deploy/run.sh [run|loop|status|test] [其它参数...]
set -euo pipefail

# 切到项目根目录（本脚本上一级）
cd "$(dirname "$0")/.."

# 如使用虚拟环境，取消下一行注释
# source .venv/bin/activate

PY="${PYTHON:-python3}"
if [ "$#" -eq 0 ]; then
  set -- run
fi
exec "$PY" main.py "$@" --config config.yaml
