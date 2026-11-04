#!/usr/bin/env bash
# 在 macOS 上打开正确地址（带 8090 端口，避免 localhost:80 的 SSH 空响应）
set -euo pipefail
URL="http://127.0.0.1:8090/"
for i in $(seq 1 30); do
  if curl -s -o /dev/null -w "%{http_code}" --max-time 1 "$URL" 2>/dev/null | grep -q 200; then
    open "$URL"
    echo "已打开 $URL"
    exit 0
  fi
  sleep 1
done
echo "服务未就绪。请先在另一终端运行: cd $(cd "$(dirname "$0")/.." && pwd) && ./scripts/dev-serve.sh" >&2
exit 1
