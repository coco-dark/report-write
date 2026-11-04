#!/usr/bin/env bash
# 本地 Redis（Docker）。用法: ./scripts/redis-dev.sh [start|stop|status|ping]
set -euo pipefail
NAME="${RW_REDIS_CONTAINER:-rw-redis}"
PORT="${RW_REDIS_PORT:-6379}"
IMAGE="${RW_REDIS_IMAGE:-redis:6-alpine}"

cmd="${1:-start}"
case "$cmd" in
  start)
    if docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
      echo "Redis 已在运行: $NAME (127.0.0.1:$PORT)"
      exit 0
    fi
    if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
      docker start "$NAME" >/dev/null
    else
      docker run -d --name "$NAME" -p "${PORT}:6379" "$IMAGE" >/dev/null
    fi
    docker exec "$NAME" redis-cli ping
    echo "Redis: redis://127.0.0.1:${PORT}/0"
    echo "export REPORT_WRITING_REDIS_URL=redis://127.0.0.1:${PORT}/0"
    ;;
  stop)
    docker stop "$NAME" 2>/dev/null || true
    echo "已停止 $NAME"
    ;;
  status)
    docker ps -a --filter "name=^${NAME}$" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    ;;
  ping)
    docker exec "$NAME" redis-cli ping
    ;;
  *)
    echo "用法: $0 [start|stop|status|ping]" >&2
    exit 1
    ;;
esac
