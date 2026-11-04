#!/usr/bin/env bash
# 本地 MySQL（Docker），端口对齐 config/application.local.yml 的 13306。
# 用法: ./scripts/mysql-dev.sh [start|stop|status|ping|recreate]
set -euo pipefail
NAME="${RW_MYSQL_CONTAINER:-rw-mysql}"
PORT="${RW_MYSQL_PORT:-13306}"
# 优先本地已有镜像，避免拉 docker.io 超时
IMAGE="${RW_MYSQL_IMAGE:-}"
if [[ -z "$IMAGE" ]]; then
  if docker image inspect mysql:8.0.30 >/dev/null 2>&1; then
    IMAGE="mysql:8.0.30"
  elif docker image inspect mysql:8.0 >/dev/null 2>&1; then
    IMAGE="mysql:8.0"
  else
    IMAGE="mysql:8.0.30"
  fi
fi
ROOT_PASSWORD="${RW_MYSQL_ROOT_PASSWORD:-AnYuan_Root_564}"
DATABASE="${RW_MYSQL_DATABASE:-anyuan}"
# 本机已有 anyuan-mysql（常见本地库）时可复用
LEGACY_NAME="${RW_MYSQL_LEGACY_CONTAINER:-anyuan-mysql}"
LEGACY_VOLUME="${RW_MYSQL_LEGACY_VOLUME:-anyuan-mysql-data}"

_mysql_server_args=(
  --character-set-server=utf8mb4
  --collation-server=utf8mb4_unicode_ci
  --skip-log-bin
)

_resolve_running_name() {
  if docker ps --format '{{.Names}}' | grep -qx "$LEGACY_NAME"; then
    echo "$LEGACY_NAME"
  elif docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
    echo "$NAME"
  else
    echo "$NAME"
  fi
}

_wait_mysql() {
  local cname="$1"
  echo -n "等待 MySQL 就绪"
  for _ in $(seq 1 40); do
    if docker exec "$cname" mysqladmin ping -h127.0.0.1 -uroot -p"${ROOT_PASSWORD}" --silent >/dev/null 2>&1; then
      echo " ok"
      echo "MySQL: 127.0.0.1:${PORT}/${DATABASE} (root, binlog=off)"
      return 0
    fi
    echo -n "."
    sleep 2
  done
  echo
  echo "MySQL 启动超时，请检查: docker logs $cname" >&2
  return 1
}

_log_bin_enabled() {
  local cname="$1"
  local val
  val="$(
    docker exec "$cname" mysql -N -h127.0.0.1 -uroot -p"${ROOT_PASSWORD}" \
      -e "SHOW VARIABLES LIKE 'log_bin';" 2>/dev/null | awk '{print $2}'
  )" || true
  [[ "$val" == "ON" || "$val" == "on" ]]
}

_recreate_with_volume() {
  local cname="$1"
  local vol="$2"
  local img="${3:-$IMAGE}"
  docker stop "$cname" 2>/dev/null || true
  docker rm "$cname" 2>/dev/null || true
  docker run -d --name "$cname" \
    -p "${PORT}:3306" \
    -v "${vol}:/var/lib/mysql" \
    -e "MYSQL_ROOT_PASSWORD=${ROOT_PASSWORD}" \
    -e "MYSQL_DATABASE=${DATABASE}" \
    -e "TZ=Asia/Shanghai" \
    "$img" \
    "${_mysql_server_args[@]}" \
    >/dev/null
  _wait_mysql "$cname"
}

_run_new_container() {
  local cname="$1"
  docker run -d --name "$cname" \
    -p "${PORT}:3306" \
    -e "MYSQL_ROOT_PASSWORD=${ROOT_PASSWORD}" \
    -e "MYSQL_DATABASE=${DATABASE}" \
    -e "TZ=Asia/Shanghai" \
    "$IMAGE" \
    "${_mysql_server_args[@]}" \
    >/dev/null
}

cmd="${1:-start}"
case "$cmd" in
  start)
    if docker ps --format '{{.Names}}' | grep -qx "$NAME"; then
      echo "MySQL 已在运行: $NAME (127.0.0.1:$PORT)"
      exit 0
    fi
    # 优先拉起已有 anyuan-mysql（数据在）
    if docker ps -a --format '{{.Names}}' | grep -qx "$LEGACY_NAME"; then
      docker start "$LEGACY_NAME" >/dev/null
      echo "已启动已有容器: $LEGACY_NAME"
      NAME="$LEGACY_NAME"
      if _log_bin_enabled "$NAME"; then
        echo "检测到 binlog 仍开启，保留数据卷 ${LEGACY_VOLUME} 重建容器（--skip-log-bin）…"
        img="$(docker inspect "$LEGACY_NAME" --format '{{.Config.Image}}' 2>/dev/null || echo "$IMAGE")"
        _recreate_with_volume "$LEGACY_NAME" "$LEGACY_VOLUME" "$img"
        exit 0
      fi
    elif docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
      docker start "$NAME" >/dev/null
      if _log_bin_enabled "$NAME"; then
        echo "警告: $NAME 仍开启 binlog。可执行: $0 recreate" >&2
      fi
    else
      _run_new_container "$NAME"
    fi
    _wait_mysql "$NAME"
    ;;
  recreate)
    cname="$LEGACY_NAME"
    vol="$LEGACY_VOLUME"
    if docker ps -a --format '{{.Names}}' | grep -qx "$NAME"; then
      cname="$NAME"
      vol="$(docker inspect "$NAME" --format '{{range .Mounts}}{{if eq .Destination "/var/lib/mysql"}}{{.Name}}{{end}}{{end}}')"
      if [[ -z "$vol" ]]; then
        echo "容器 $NAME 无命名卷，无法无损 recreate。" >&2
        exit 1
      fi
    fi
    img="$(docker inspect "$cname" --format '{{.Config.Image}}' 2>/dev/null || echo "$IMAGE")"
    echo "重建 ${cname}（卷 ${vol}，关闭 binlog）…"
    _recreate_with_volume "$cname" "$vol" "$img"
    ;;
  stop)
    docker stop "$LEGACY_NAME" 2>/dev/null || true
    docker stop "$NAME" 2>/dev/null || true
    echo "已停止 MySQL 容器"
    ;;
  status)
    docker ps -a --filter "name=${LEGACY_NAME}|^${NAME}$" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
    ;;
  ping)
    cname="$(_resolve_running_name)"
    docker exec "$cname" mysqladmin ping -h127.0.0.1 -uroot -p"${ROOT_PASSWORD}" --silent
    echo "mysqld is alive"
    ;;
  *)
    echo "用法: $0 [start|stop|status|ping|recreate]" >&2
    exit 1
    ;;
esac
