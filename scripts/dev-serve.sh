#!/usr/bin/env bash
# 本地开发：先起 MySQL/Redis，再启动 Web 服务；异常退出时自动拉起。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PORT="${REPORT_WRITING_PORT:-8090}"
HOST="${REPORT_WRITING_HOST:-0.0.0.0}"
LOG="${REPORT_WRITING_SERVE_LOG:-/tmp/rw-serve.log}"
export REPORT_WRITING_REDIS_URL="${REPORT_WRITING_REDIS_URL:-redis://127.0.0.1:6379/0}"
export REPORT_WRITING_RELOAD="${REPORT_WRITING_RELOAD:-1}"
export PYTHONPATH="${ROOT}/backend/src${PYTHONPATH:+:$PYTHONPATH}"
PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "未找到 .venv，请先: python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi

# MySQL(13306) + Redis(6379)；可用 REPORT_WRITING_SKIP_DEPS=1 跳过
if [[ "${REPORT_WRITING_SKIP_DEPS:-0}" != "1" ]]; then
  "$ROOT/scripts/ensure-deps.sh"
fi

LOCK_DIR="${REPORT_WRITING_SERVE_LOCK_DIR:-/tmp/rw-serve.lock.d}"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
  echo "已有 dev-serve 在运行（锁 ${LOCK_DIR}）。请勿重复启动；" >&2
  echo "若要强制重启：先停掉旧终端里的 dev-serve，或 rm -rf ${LOCK_DIR} 后再启动。" >&2
  exit 1
fi
trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT INT TERM

echo "报告书写 dev-serve → http://127.0.0.1:${PORT}/ （热更新 REPORT_WRITING_RELOAD=${REPORT_WRITING_RELOAD}，日志 ${LOG}）"
echo "依赖: MySQL 127.0.0.1:13306 · Redis 127.0.0.1:6379"
echo "按 Ctrl+C 停止；请勿在其它终端反复 pkill，否则会断服。"

while true; do
  if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
    echo "--- $(date '+%Y-%m-%d %H:%M:%S') 端口 ${PORT} 已被占用，跳过本轮启动（可能另有 serve）---" | tee -a "$LOG"
    sleep 5
    continue
  fi
  echo "--- $(date '+%Y-%m-%d %H:%M:%S') 启动 serve ---" | tee -a "$LOG"
  "$PY" -m report_writing serve --host "$HOST" --port "$PORT" --reload 2>&1 | tee -a "$LOG" || true
  echo "--- $(date '+%Y-%m-%d %H:%M:%S') 进程退出，5 秒后重启 ---" | tee -a "$LOG"
  sleep 5
done
