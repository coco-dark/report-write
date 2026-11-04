#!/usr/bin/env bash
# 确保 Docker（优先 Colima）可用，并启动本项目依赖的 MySQL + Redis。
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

wait_docker() {
  if docker info >/dev/null 2>&1; then
    return 0
  fi
  if command -v colima >/dev/null 2>&1; then
    echo "正在用 Colima 启动 Docker…"
    # 已在跑则 start 会很快返回；未跑则拉起 VM
    colima start
    for _ in $(seq 1 60); do
      if docker info >/dev/null 2>&1; then
        echo "Docker（Colima）已就绪"
        return 0
      fi
      sleep 2
    done
    echo "Colima 已启动但 docker 仍不可用，请执行: colima status && docker info" >&2
    exit 1
  fi
  echo "未找到 colima。正在尝试 Docker Desktop…"
  open -a Docker >/dev/null 2>&1 || true
  for _ in $(seq 1 60); do
    if docker info >/dev/null 2>&1; then
      echo "Docker 已就绪"
      return 0
    fi
    sleep 2
  done
  echo "Docker 未就绪。请安装/启动 Colima：brew install colima docker && colima start" >&2
  exit 1
}

# 本项目只用这两个（或旧名 anyuan-mysql）。其它 compose（如 Dify）一律停掉并删除容器。
KEEP_CONTAINERS="${RW_KEEP_CONTAINERS:-anyuan-mysql rw-mysql rw-redis}"

prune_foreign_containers() {
  local keep=" ${KEEP_CONTAINERS} "
  local id name
  docker ps -aq --format '{{.ID}} {{.Names}}' | while read -r id name; do
    [[ -n "$id" && -n "$name" ]] || continue
    case " $keep " in
      *" $name "*) continue ;;
    esac
    echo "停并删除无关容器: $name"
    docker update --restart=no "$id" >/dev/null 2>&1 || true
    docker rm -f "$id" >/dev/null 2>&1 || true
  done
}

wait_docker
prune_foreign_containers
"$ROOT/scripts/mysql-dev.sh" start
"$ROOT/scripts/redis-dev.sh" start
prune_foreign_containers
