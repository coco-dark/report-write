#!/usr/bin/env bash
# 本地全栈开发：MySQL + Redis + 后端 uvicorn --reload + 前端 Vite HMR
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PORT="${REPORT_WRITING_PORT:-8090}"
UI_PORT="${REPORT_WRITING_UI_PORT:-5173}"
export REPORT_WRITING_RELOAD=1
export REPORT_WRITING_REDIS_URL="${REPORT_WRITING_REDIS_URL:-redis://127.0.0.1:6379/0}"
export PYTHONPATH="${ROOT}/backend/src${PYTHONPATH:+:$PYTHONPATH}"
PY="${ROOT}/.venv/bin/python"

if [[ ! -x "$PY" ]]; then
  echo "未找到 .venv，请先: python3 -m venv .venv && .venv/bin/pip install -e ." >&2
  exit 1
fi
if [[ ! -d "${ROOT}/frontend/node_modules" ]]; then
  echo "未找到 frontend/node_modules，请先: cd frontend && npm install" >&2
  exit 1
fi

if [[ "${REPORT_WRITING_SKIP_DEPS:-0}" != "1" ]]; then
  "$ROOT/scripts/ensure-deps.sh"
fi

cleanup() {
  [[ -n "${BACK_PID:-}" ]] && kill "$BACK_PID" 2>/dev/null || true
  [[ -n "${UI_PID:-}" ]] && kill "$UI_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

echo "后端（热更新）→ http://127.0.0.1:${PORT}/"
echo "前端（Vite HMR）→ http://127.0.0.1:${UI_PORT}/ （/api 代理到 ${PORT}）"
echo "依赖: MySQL 127.0.0.1:13306 · Redis 127.0.0.1:6379"
echo "按 Ctrl+C 停止"

"$PY" -m report_writing serve --host 0.0.0.0 --port "$PORT" --reload &
BACK_PID=$!

cd "${ROOT}/frontend"
npm run dev -- --host 127.0.0.1 --port "$UI_PORT" &
UI_PID=$!

wait
