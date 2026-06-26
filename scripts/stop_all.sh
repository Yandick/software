#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT_DIR/.run"

BACKEND_PORT="${BACKEND_PORT:-8010}"
FRONTEND_PORT="${FRONTEND_PORT:-5666}"
VLLM_PORT="${OPS_VLLM_PORT:-8000}"

SERVICE_NAMES=(frontend backend vllm)
LEGACY_PID_FILES=(
  "$RUN_DIR/frontend-local.pid"
  "$RUN_DIR/backend-local.pid"
  "$RUN_DIR/vllm-local.pid"
  "$RUN_DIR/frontend-auto-pipeline.pid"
  "$RUN_DIR/backend-auto-pipeline.pid"
  "$RUN_DIR/vllm-auto-pipeline.pid"
  "$RUN_DIR/frontend-portal.pid"
  "$RUN_DIR/backend-portal.pid"
  "$RUN_DIR/vllm-portal.pid"
)

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

kill_vllm_orphans() {
  local pid
  if ! command -v pgrep >/dev/null 2>&1; then
    return 0
  fi
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    if kill -0 "$pid" >/dev/null 2>&1; then
      log "stopping lingering vLLM worker pid=$pid"
      kill -TERM "$pid" >/dev/null 2>&1 || true
    fi
  done < <(pgrep -u "$USER" -f 'VLLM::Worker_TP[0-9]+' 2>/dev/null || true)
  sleep 1
  while IFS= read -r pid; do
    [[ -z "$pid" ]] && continue
    if kill -0 "$pid" >/dev/null 2>&1; then
      log "force stopping lingering vLLM worker pid=$pid"
      kill -KILL "$pid" >/dev/null 2>&1 || true
    fi
  done < <(pgrep -u "$USER" -f 'VLLM::Worker_TP[0-9]+' 2>/dev/null || true)
}

pid_is_project_owned() {
  local pid="$1" cwd cmdline
  cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
  cmdline="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)"
  [[ "$cwd" == "$ROOT_DIR" || "$cwd" == "$ROOT_DIR/frontend" || "$cmdline" == *"$ROOT_DIR"* || "$cmdline" == *"backend.app.main"* || "$cmdline" == *"vllm.entrypoints.openai.api_server"* ]]
}

stop_pid() {
  local pid="$1" label="$2"
  if [[ -z "$pid" || ! "$pid" =~ ^[0-9]+$ ]]; then
    return 0
  fi
  if ! kill -0 "$pid" >/dev/null 2>&1; then
    return 0
  fi
  log "stopping $label pid=$pid"
  pkill -TERM -s "$pid" >/dev/null 2>&1 || true
  kill -- "-$pid" >/dev/null 2>&1 || kill "$pid" >/dev/null 2>&1 || true
}

kill_pid_if_alive() {
  local pid="$1" label="$2"
  if [[ -n "$pid" && "$pid" =~ ^[0-9]+$ ]] && kill -0 "$pid" >/dev/null 2>&1; then
    log "force stopping $label pid=$pid"
    pkill -KILL -s "$pid" >/dev/null 2>&1 || true
    kill -- "-$pid" >/dev/null 2>&1 || kill -KILL "$pid" >/dev/null 2>&1 || true
  fi
}

stop_pid_file() {
  local pid_file="$1" label="${2:-runtime}" pid
  if [[ ! -f "$pid_file" ]]; then
    return 0
  fi
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -n "$pid" ]]; then
    if [[ ! "$pid" =~ ^[0-9]+$ ]]; then
      log "skip invalid pid file $pid_file: pid=$pid"
      rm -f "$pid_file"
      return 0
    fi
    if kill -0 "$pid" >/dev/null 2>&1 && ! pid_is_project_owned "$pid"; then
      local cwd
      cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
      log "skip non-project pid file $pid_file: pid=$pid cwd=$cwd"
      rm -f "$pid_file"
      return 0
    fi
    stop_pid "$pid" "$label"
    sleep 1
    kill_pid_if_alive "$pid" "$label"
  fi
  rm -f "$pid_file"
}

pids_on_port() {
  local port="$1"
  if ! command -v ss >/dev/null 2>&1; then
    return 0
  fi
  ss -ltnp "sport = :$port" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true
}

stop_project_port() {
  local port="$1" label="$2" pids pid
  mapfile -t pids < <(pids_on_port "$port")
  for pid in "${pids[@]}"; do
    if pid_is_project_owned "$pid"; then
      stop_pid "$pid" "$label port $port"
    else
      log "skip non-project process on $label port $port: pid=$pid"
      ps -fp "$pid" || true
    fi
  done
  sleep 1
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" >/dev/null 2>&1 && pid_is_project_owned "$pid"; then
      kill_pid_if_alive "$pid" "$label port $port"
    fi
  done
}

main() {
  local name pid_file
  mkdir -p "$RUN_DIR"

  for name in "${SERVICE_NAMES[@]}"; do
    stop_pid_file "$RUN_DIR/$name.pid" "$name"
  done
  for pid_file in "${LEGACY_PID_FILES[@]}"; do
    stop_pid_file "$pid_file" "legacy $(basename "$pid_file" .pid)"
  done

  stop_project_port "$FRONTEND_PORT" frontend
  stop_project_port "$BACKEND_PORT" backend
  stop_project_port "$VLLM_PORT" vLLM
  kill_vllm_orphans

  log "done"
}

main "$@"
