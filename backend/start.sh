#!/usr/bin/env bash
# 一键启动后端：接口、解析 worker、索引 worker、维护 worker、定时任务、Flower。
# 在仓库根目录执行 ./backend/start.sh，或在 backend 目录执行 ./start.sh。
# Ctrl+C 会停掉这次启动的全部进程。

set -euo pipefail
set -m

root="$(cd "$(dirname "$0")" && pwd)"
cd "$root"

if [[ ! -f .env ]]; then
  echo "缺少 ${root}/.env，先复制 .env.example 并填上本机配置。" >&2
  exit 1
fi

if ! command -v uv >/dev/null 2>&1; then
  echo "未找到 uv。" >&2
  exit 1
fi

load_env() {
  local line key value
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" != *=* ]] && continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key#"${key%%[![:space:]]*}"}"
    key="${key%"${key##*[![:space:]]}"}"
    if [[ ${#value} -ge 2 && "$value" == \"*\" && "$value" == *\" ]]; then
      value="${value:1:${#value}-2}"
    elif [[ ${#value} -ge 2 && "$value" == \'*\' && "$value" == *\' ]]; then
      value="${value:1:${#value}-2}"
    fi
    export "${key}=${value}"
  done < .env
}

load_env

export PYTHONUNBUFFERED=1

api_port="${API_PORT:-8000}"
flower_address="${FLOWER_ADDRESS:-0.0.0.0}"
flower_port="${FLOWER_PORT:-5555}"

port_busy() {
  local port="$1"
  if command -v ss >/dev/null 2>&1; then
    ss -ltn | grep -qE ":${port}[[:space:]]"
    return
  fi
  return 1
}

if port_busy "$api_port"; then
  echo "端口 ${api_port} 已被占用。先停掉正在跑的接口，再执行本脚本。" >&2
  exit 1
fi

if port_busy "$flower_port"; then
  echo "端口 ${flower_port} 已被占用。先停掉正在跑的 Flower，再执行本脚本。" >&2
  exit 1
fi

mkdir -p .run

pids=()

kill_tree() {
  local pid="$1"
  local signal="$2"
  local child
  for child in $(pgrep -P "$pid" || true); do
    kill_tree "$child" "$signal"
  done
  kill "-${signal}" "$pid" 2>/dev/null || true
  kill "-${signal}" -- "-${pid}" 2>/dev/null || true
}

cleanup() {
  local pid
  [[ ${#pids[@]} -eq 0 ]] && return
  echo
  echo "正在停止后端进程"
  for pid in "${pids[@]}"; do
    kill_tree "$pid" TERM
  done
  sleep 1
  for pid in "${pids[@]}"; do
    kill_tree "$pid" KILL
  done
  wait 2>/dev/null || true
  pids=()
}

on_stop() {
  cleanup
  exit 0
}

trap on_stop INT TERM
trap cleanup EXIT

run_prefixed() {
  local name="$1"
  shift
  "$@" 2>&1 | while IFS= read -r line; do
    printf '[%s] %s\n' "$name" "$line"
  done
}

start() {
  local name="$1"
  shift
  echo "启动 ${name}"
  run_prefixed "$name" "$@" &
  pids+=("$!")
}

start "接口" uv run uvicorn main:app --reload --host 0.0.0.0 --port "$api_port"
start "解析" uv run celery -A main.celery_app worker \
  --loglevel=info \
  -Q celery \
  -n "parse@%h"
start "索引" uv run celery -A main.celery_app worker \
  --loglevel=info \
  -Q index \
  --concurrency=2 \
  -n "index@%h"
start "维护" uv run celery -A main.celery_app worker \
  --loglevel=info \
  -Q maintenance \
  --concurrency=1 \
  -n "maintenance@%h"
start "定时" uv run celery -A main.celery_app beat \
  --loglevel=info \
  --schedule "${root}/.run/celerybeat-schedule"
start "监控" uv run celery -A main.celery_app flower

echo "接口文档 http://127.0.0.1:${api_port}/docs"
echo "Flower http://127.0.0.1:${flower_port}"
echo "按 Ctrl+C 停止"

status=0
wait -n || status=$?
exit "$status"
