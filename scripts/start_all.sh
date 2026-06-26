#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs"
RUN_DIR="$ROOT_DIR/.run"
mkdir -p "$LOG_DIR" "$RUN_DIR"

load_env_defaults() {
  local env_file="$ROOT_DIR/.env" line key value
  [[ -f "$env_file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" == *"="* ]] || continue
    key="${line%%=*}"
    value="${line#*=}"
    key="${key//[[:space:]]/}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    value="${value#\"}"
    value="${value%\"}"
    value="${value#\'}"
    value="${value%\'}"
    if [[ -z "${!key+x}" ]]; then
      export "$key=$value"
    fi
  done <"$env_file"
}

load_env_defaults

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8010}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-5666}"
VLLM_HOST="${OPS_VLLM_HOST:-127.0.0.1}"
VLLM_PORT="${OPS_VLLM_PORT:-8000}"
MODEL_PATH="${OPS_MODEL_PATH:-models/qwen3-1.7b}"
SERVED_MODEL_NAME="${OPS_VLLM_MODEL_NAME:-qwen3-1.7b}"
VLLM_REASONING_PARSER="${VLLM_REASONING_PARSER:-qwen3}"
PYTHON_BIN="${PYTHON_BIN:-python}"
PNPM_VERSION="${PNPM_VERSION:-10.33.0}"
BASE_CUDA_DEVICES="${OPS_BASE_CUDA_VISIBLE_DEVICES:-0}"
EMBEDDING_CUDA_DEVICES="${OPS_EMBEDDING_CUDA_VISIBLE_DEVICES:-$BASE_CUDA_DEVICES}"
NCCL_IB_DISABLE="${NCCL_IB_DISABLE:-1}"
NCCL_NET="${NCCL_NET:-Socket}"
NCCL_DEBUG="${NCCL_DEBUG:-WARN}"

START_FRONTEND=1
INSTALL_FRONTEND=0
VLLM_TIMEOUT="${VLLM_TIMEOUT:-420}"
BACKEND_TIMEOUT="${BACKEND_TIMEOUT:-90}"
VLLM_GPU_MEMORY_UTILIZATION="${VLLM_GPU_MEMORY_UTILIZATION:-}"
VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-40960}"

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

usage() {
  cat <<USAGE
Usage: scripts/start_all.sh --base-cuda-devices 0,1 --embedding-cuda-devices 2 [options]

Options:
  --no-frontend         只启动 vLLM + 后端
  --install-frontend    启动前执行 pnpm install
  --base-cuda-devices VALUE       设置 vLLM/base model 使用的 GPU，例如 0、1、0,1
  --embedding-cuda-devices VALUE  设置后端 embedding model 使用的 GPU
  -h, --help            显示帮助

Env:
  BACKEND_PORT          后端端口，默认 8010
  FRONTEND_PORT         前端端口，默认 5666
  OPS_BASE_CUDA_VISIBLE_DEVICES   指定 vLLM/base model GPU
  OPS_EMBEDDING_CUDA_VISIBLE_DEVICES  指定 embedding model GPU；设置后后端只看到这些 GPU
  OPS_EMBEDDING_DEVICE  embedding torch device；默认 auto，配合 OPS_EMBEDDING_CUDA_VISIBLE_DEVICES 时可保持 auto
  OPS_MODEL_PATH        模型路径，默认 models/qwen3-1.7b
  OPS_VLLM_MODEL_NAME   vLLM served model name，默认 qwen3-1.7b
  VLLM_REASONING_PARSER vLLM reasoning parser，默认 qwen3
  VLLM_TIMEOUT          等待 vLLM 秒数，默认 420
  VLLM_GPU_MEMORY_UTILIZATION  vLLM 显存利用率；未设置时按模型规模和上下文自动估算
  VLLM_MAX_MODEL_LEN    vLLM 最大上下文长度，默认 40960；显存紧张可设为 8192/16384
  VLLM_TENSOR_PARALLEL_SIZE    vLLM tensor parallel 大小；默认按 vLLM GPU 数量推导
  VLLM_MAX_NUM_SEQS     vLLM 最大并发序列数；未设置时按 base GPU 数量估算
  VLLM_MAX_NUM_BATCHED_TOKENS  vLLM 每批 token 上限；未设置时按 base GPU 数量估算
  NCCL_IB_DISABLE      默认 1；单机多卡默认关闭 IB，避免 NCCL net plugin 在无 IB 环境崩溃
  NCCL_NET             默认 Socket；单机多卡默认使用 socket bootstrap
  OPS_ENABLE_AGENT_LLM  是否启用真实 subagent LLM 审阅；默认 true
  OPS_AGENT_LLM_PARALLELISM  subagent LLM 并行审阅数量；默认 5
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-frontend) START_FRONTEND=0 ;;
    --install-frontend) INSTALL_FRONTEND=1 ;;
    --base-cuda-devices)
      shift
      if [[ $# -eq 0 ]]; then echo "base cuda devices option requires a value"; exit 2; fi
      BASE_CUDA_DEVICES="$1"
      ;;
    --embedding-cuda-devices)
      shift
      if [[ $# -eq 0 ]]; then echo "--embedding-cuda-devices requires a value"; exit 2; fi
      EMBEDDING_CUDA_DEVICES="$1"
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 2 ;;
  esac
  shift
done

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
have_cmd() { command -v "$1" >/dev/null 2>&1; }

require_cmd() {
  if ! have_cmd "$1"; then
    log "缺少命令：$1。请先激活项目 conda 环境并安装依赖。"
    exit 1
  fi
}

run_python_bg() {
  local name="$1"; shift
  local quoted_args
  printf -v quoted_args ' %q' "$@"
  setsid bash -c "cd '$ROOT_DIR' && exec '$PYTHON_BIN'$quoted_args" >"$LOG_DIR/$name.log" 2>&1 &
  echo $! >"$RUN_DIR/$name.pid"
  log "$name started, pid=$(cat "$RUN_DIR/$name.pid"), log=logs/$name.log"
}

pid_is_project_owned() {
  local pid="$1" cwd cmdline
  if [[ ! -d "/proc/$pid" ]]; then
    return 1
  fi
  cwd="$(readlink "/proc/$pid/cwd" 2>/dev/null || true)"
  if [[ -r "/proc/$pid/cmdline" ]]; then
    cmdline="$(tr '\0' ' ' <"/proc/$pid/cmdline" 2>/dev/null || true)"
  else
    cmdline=""
  fi
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

stop_known_pid_files() {
  local name pid_file
  for name in "${SERVICE_NAMES[@]}"; do
    stop_pid_file "$RUN_DIR/$name.pid" "$name"
  done
  for pid_file in "${LEGACY_PID_FILES[@]}"; do
    stop_pid_file "$pid_file" "legacy $(basename "$pid_file" .pid)"
  done
}

pnpm_cmd() {
  if have_cmd pnpm; then
    pnpm "$@"
  elif have_cmd corepack; then
    corepack pnpm "$@"
  elif have_cmd npm; then
    npm exec --yes "pnpm@$PNPM_VERSION" -- "$@"
  else
    log "缺少 pnpm/corepack/npm。请先在当前环境安装 Node.js 和 pnpm。"
    return 127
  fi
}

pnpm_start_command() {
  local cmd args
  args=(--filter ops-employee-frontend dev --host "$FRONTEND_HOST" --port "$FRONTEND_PORT")
  if have_cmd pnpm; then
    cmd=("$(command -v pnpm)" "${args[@]}")
  elif have_cmd corepack; then
    cmd=("$(command -v corepack)" pnpm "${args[@]}")
  elif have_cmd npm; then
    cmd=("$(command -v npm)" exec --yes "pnpm@$PNPM_VERSION" -- "${args[@]}")
  else
    log "缺少 pnpm/corepack/npm。请先在当前环境安装 Node.js 和 pnpm。"
    return 127
  fi
  printf '%q ' "${cmd[@]}"
}

CLEANED_UP=0
cleanup() {
  if [[ "$CLEANED_UP" == 1 ]]; then
    return
  fi
  CLEANED_UP=1
  log "stopping services..."
  for name in "${SERVICE_NAMES[@]}"; do
    stop_pid_file "$RUN_DIR/$name.pid"
  done
}
trap cleanup EXIT INT TERM

wait_http() {
  local url="$1" timeout="$2" label="$3" pid_file="${4:-}"
  local start now pid
  start="$(date +%s)"
  while true; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      log "$label is ready: $url"
      return 0
    fi
    if [[ -n "$pid_file" && -f "$pid_file" ]]; then
      pid="$(cat "$pid_file" 2>/dev/null || true)"
      if [[ -n "$pid" ]] && ! kill -0 "$pid" >/dev/null 2>&1; then
        log "$label process exited before ready. 查看 logs/$label.log"
        return 2
      fi
    fi
    now="$(date +%s)"
    if (( now - start >= timeout )); then
      log "$label not ready after ${timeout}s: $url"
      return 1
    fi
    sleep 2
  done
}

pids_on_port() {
  local port="$1"
  if ! command -v ss >/dev/null 2>&1; then
    return 0
  fi
  ss -ltnp "sport = :$port" 2>/dev/null | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true
}

ensure_port_available() {
  local port="$1" label="$2" pids pid blockers=()
  mapfile -t pids < <(pids_on_port "$port")
  if [[ "${#pids[@]}" -eq 0 ]]; then
    return 0
  fi

  for pid in "${pids[@]}"; do
    if pid_is_project_owned "$pid"; then
      stop_pid "$pid" "$label port $port"
    else
      blockers+=("$pid")
    fi
  done
  sleep 1
  for pid in "${pids[@]}"; do
    if pid_is_project_owned "$pid"; then
      kill_pid_if_alive "$pid" "$label port $port"
    fi
  done

  mapfile -t pids < <(pids_on_port "$port")
  blockers=()
  for pid in "${pids[@]}"; do
    if kill -0 "$pid" >/dev/null 2>&1; then
      blockers+=("$pid")
    fi
  done
  if [[ "${#blockers[@]}" -gt 0 ]]; then
    log "$label port $port is already in use by non-project process(es): ${blockers[*]}"
    for pid in "${blockers[@]}"; do
      ps -fp "$pid" || true
    done
    log "请先释放端口或修改环境变量：BACKEND_PORT / FRONTEND_PORT / OPS_VLLM_PORT。"
    exit 1
  fi
}

first_cuda_device() {
  local value="$1"
  value="${value%%,*}"
  value="${value//[[:space:]]/}"
  echo "$value"
}

count_cuda_devices() {
  local value="$1"
  if [[ -z "$value" ]]; then
    echo 1
    return
  fi
  awk -F',' '{print NF}' <<<"$value"
}

model_params_billion() {
  local value
  value="$(basename "$MODEL_PATH" | tr '[:upper:]' '[:lower:]')"
  "$PYTHON_BIN" - "$value" <<'PY'
import re
import sys
name = sys.argv[1]
match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*b\b", name.replace("-", " "))
if not match:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)b", name)
print(match.group(1) if match else "0")
PY
}

auto_vllm_gpu_memory_utilization() {
  local params_b="$1" max_model_len="$2" tp_size="$3"
  "$PYTHON_BIN" - "$params_b" "$max_model_len" "$tp_size" <<'PY'
import sys
params = float(sys.argv[1] or 0)
max_len = int(float(sys.argv[2] or 0))
tp = max(1, int(float(sys.argv[3] or 1)))
if params <= 0:
    util = 0.60
elif params <= 2:
    util = 0.58 if max_len >= 32768 else 0.50
elif params <= 4:
    util = 0.64 if max_len >= 32768 else 0.58
elif params <= 8:
    util = 0.74
elif params <= 14:
    util = 0.84
else:
    util = 0.90
if tp >= 2 and params <= 4 and max_len >= 32768:
    util = min(0.62, util + 0.03)
print(f"{util:.2f}")
PY
}

visible_cuda_devices() {
  local value="$1"
  IFS=',' read -ra devices <<<"$value"
  for device in "${devices[@]}"; do
    device="${device//[[:space:]]/}"
    if [[ -n "$device" ]]; then
      echo "$device"
    fi
  done
}

check_gpu_memory() {
  local cuda_devices="$1" utilization="$2" label="$3"
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    log "nvidia-smi 不可用，跳过显存预检查。"
    return 0
  fi
  local gpu_id free_mib total_mib used_mib required_mib line
  if [[ "$cuda_devices" != *","* && ! "$cuda_devices" =~ ^[0-9]+$ ]]; then
    log "$label CUDA_VISIBLE_DEVICES=$cuda_devices 不是纯 GPU 编号，跳过显存预检查。"
    return 0
  fi
  while read -r gpu_id; do
    [[ -z "$gpu_id" ]] && continue
    if [[ ! "$gpu_id" =~ ^[0-9]+$ ]]; then
      log "$label CUDA_VISIBLE_DEVICES 包含非数字 GPU 标识：$gpu_id，跳过显存预检查。"
      return 0
    fi
    line="$(nvidia-smi --query-gpu=index,memory.total,memory.used,memory.free --format=csv,noheader,nounits | awk -F', ' -v id="$gpu_id" '$1 == id {print $0}')"
    if [[ -z "$line" ]]; then
      log "未找到 GPU $gpu_id，请检查 $label GPU 配置。"
      exit 1
    fi
    IFS=', ' read -r _ total_mib used_mib free_mib <<<"$line"
    required_mib="$($PYTHON_BIN - <<PY2
print(int(float('$total_mib') * float('$utilization')))
PY2
)"
    log "$label GPU $gpu_id memory: total=${total_mib}MiB used=${used_mib}MiB free=${free_mib}MiB; target=${required_mib}MiB"
    if (( free_mib < required_mib )); then
      log "GPU $gpu_id 空闲显存不足以按 utilization=$utilization 启动 $label。"
      log "请先运行 nvidia-smi 选择空闲 GPU，例如：./scripts/start_all.sh --base-cuda-devices 0,1 --embedding-cuda-devices 2"
      log "如果必须使用当前 GPU，可降低上下文或显存比例，例如：VLLM_MAX_MODEL_LEN=8192 VLLM_GPU_MEMORY_UTILIZATION=0.35 ./scripts/start_all.sh --base-cuda-devices $gpu_id --embedding-cuda-devices $EMBEDDING_CUDA_DEVICES"
      exit 1
    fi
  done < <(visible_cuda_devices "$cuda_devices")
}

cd "$ROOT_DIR"
export BACKEND_HOST BACKEND_PORT FRONTEND_HOST FRONTEND_PORT

require_cmd "$PYTHON_BIN"
require_cmd curl
if [[ "$START_FRONTEND" == 1 ]]; then
  require_cmd node
  if ! have_cmd pnpm && ! have_cmd corepack && ! have_cmd npm; then
    log "缺少 pnpm/corepack/npm。请先安装前端依赖工具。"
    exit 1
  fi
fi

log "project root: $ROOT_DIR"
log "python: $(command -v "$PYTHON_BIN")"
log "node: $([[ "$START_FRONTEND" == 1 ]] && command -v node || echo 'not required')"
VLLM_TENSOR_PARALLEL_SIZE="${VLLM_TENSOR_PARALLEL_SIZE:-$(count_cuda_devices "$BASE_CUDA_DEVICES")}"
BASE_GPU_COUNT="$(count_cuda_devices "$BASE_CUDA_DEVICES")"
if [[ "${BASE_GPU_COUNT:-1}" -le 1 ]]; then
  DEFAULT_VLLM_MAX_NUM_SEQS=8
  DEFAULT_VLLM_MAX_NUM_BATCHED_TOKENS=4096
else
  DEFAULT_VLLM_MAX_NUM_SEQS=16
  DEFAULT_VLLM_MAX_NUM_BATCHED_TOKENS=8192
fi
export OPS_ENABLE_AGENT_LLM="${OPS_ENABLE_AGENT_LLM:-true}"
export OPS_AGENT_LLM_PARALLELISM="${OPS_AGENT_LLM_PARALLELISM:-5}"
export OPS_AGENT_LLM_TIMEOUT_SECONDS="${OPS_AGENT_LLM_TIMEOUT_SECONDS:-45}"
VLLM_MAX_NUM_SEQS="${VLLM_MAX_NUM_SEQS:-$DEFAULT_VLLM_MAX_NUM_SEQS}"
VLLM_MAX_NUM_BATCHED_TOKENS="${VLLM_MAX_NUM_BATCHED_TOKENS:-$DEFAULT_VLLM_MAX_NUM_BATCHED_TOKENS}"
if [[ -z "$VLLM_GPU_MEMORY_UTILIZATION" ]]; then
  MODEL_PARAMS_B="$(model_params_billion)"
  VLLM_GPU_MEMORY_UTILIZATION="$(auto_vllm_gpu_memory_utilization "$MODEL_PARAMS_B" "$VLLM_MAX_MODEL_LEN" "$VLLM_TENSOR_PARALLEL_SIZE")"
  VLLM_GPU_MEMORY_UTILIZATION_SOURCE="auto(model_params=${MODEL_PARAMS_B}B)"
else
  VLLM_GPU_MEMORY_UTILIZATION_SOURCE="manual"
fi
log "vLLM CUDA_VISIBLE_DEVICES=$BASE_CUDA_DEVICES"
log "backend/embedding CUDA_VISIBLE_DEVICES=$EMBEDDING_CUDA_DEVICES"
log "subagent LLM: OPS_ENABLE_AGENT_LLM=$OPS_ENABLE_AGENT_LLM parallelism=$OPS_AGENT_LLM_PARALLELISM"
log "vLLM memory config: gpu_memory_utilization=$VLLM_GPU_MEMORY_UTILIZATION [$VLLM_GPU_MEMORY_UTILIZATION_SOURCE], max_model_len=$VLLM_MAX_MODEL_LEN, tensor_parallel_size=$VLLM_TENSOR_PARALLEL_SIZE"
log "vLLM concurrency: max_num_seqs=$VLLM_MAX_NUM_SEQS, max_num_batched_tokens=$VLLM_MAX_NUM_BATCHED_TOKENS"
log "NCCL config for vLLM: NCCL_IB_DISABLE=$NCCL_IB_DISABLE, NCCL_NET=$NCCL_NET, NCCL_DEBUG=$NCCL_DEBUG"

log "stopping existing project processes..."
BACKEND_PORT="$BACKEND_PORT" FRONTEND_PORT="$FRONTEND_PORT" OPS_VLLM_PORT="$VLLM_PORT" "$ROOT_DIR/scripts/stop_all.sh"
ensure_port_available "$BACKEND_PORT" backend
if [[ "$START_FRONTEND" == 1 ]]; then ensure_port_available "$FRONTEND_PORT" frontend; fi
ensure_port_available "$VLLM_PORT" vLLM
check_gpu_memory "$BASE_CUDA_DEVICES" "$VLLM_GPU_MEMORY_UTILIZATION" "vLLM"

log "starting vLLM for Qwen3: $MODEL_PATH"
export CUDA_VISIBLE_DEVICES="$BASE_CUDA_DEVICES"
export NCCL_IB_DISABLE NCCL_NET NCCL_DEBUG
VLLM_ARGS=(
  -m vllm.entrypoints.openai.api_server
  --model "$MODEL_PATH" \
  --served-model-name "$SERVED_MODEL_NAME" \
  --host "$VLLM_HOST" \
  --port "$VLLM_PORT" \
  --reasoning-parser "$VLLM_REASONING_PARSER" \
  --gpu-memory-utilization "$VLLM_GPU_MEMORY_UTILIZATION" \
  --max-model-len "$VLLM_MAX_MODEL_LEN" \
  --tensor-parallel-size "$VLLM_TENSOR_PARALLEL_SIZE"
)
if [[ -n "${VLLM_MAX_NUM_SEQS:-}" ]]; then
  VLLM_ARGS+=(--max-num-seqs "$VLLM_MAX_NUM_SEQS")
fi
if [[ -n "${VLLM_MAX_NUM_BATCHED_TOKENS:-}" ]]; then
  VLLM_ARGS+=(--max-num-batched-tokens "$VLLM_MAX_NUM_BATCHED_TOKENS")
fi
run_python_bg vllm "${VLLM_ARGS[@]}"
if wait_http "http://$VLLM_HOST:$VLLM_PORT/v1/models" "$VLLM_TIMEOUT" vllm "$RUN_DIR/vllm.pid"; then
  export OPS_VLLM_BASE_URL="http://$VLLM_HOST:$VLLM_PORT/v1"
  export OPS_VLLM_MODEL_NAME="$SERVED_MODEL_NAME"
else
  log "vLLM 启动失败，数字员工不可用。请查看 logs/vllm.log 后重新启动。"
  exit 1
fi

log "starting backend: http://$BACKEND_HOST:$BACKEND_PORT"
export CUDA_VISIBLE_DEVICES="$EMBEDDING_CUDA_DEVICES"
export OPS_EMBEDDING_CUDA_VISIBLE_DEVICES="$EMBEDDING_CUDA_DEVICES"
run_python_bg backend -m uvicorn backend.app.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT"
wait_http "http://$BACKEND_HOST:$BACKEND_PORT/api/health" "$BACKEND_TIMEOUT" backend "$RUN_DIR/backend.pid" || exit 1

if [[ "$START_FRONTEND" == 1 ]]; then
  if [[ "$INSTALL_FRONTEND" == 1 || ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
    log "installing frontend dependencies with pnpm..."
    (cd "$ROOT_DIR/frontend" && pnpm_cmd install) 2>&1 | tee "$LOG_DIR/frontend-install.log"
  fi
  log "starting frontend: http://$FRONTEND_HOST:$FRONTEND_PORT"
  if ! frontend_cmd="$(pnpm_start_command)"; then
    log "前端启动命令解析失败。"
    exit 1
  fi
  setsid bash -c "cd '$ROOT_DIR/frontend' && exec $frontend_cmd" >"$LOG_DIR/frontend.log" 2>&1 &
  echo $! >"$RUN_DIR/frontend.pid"
  log "frontend started, pid=$(cat "$RUN_DIR/frontend.pid"), log=logs/frontend.log"
fi

cat <<DONE

启动完成：
- 后端:   http://$BACKEND_HOST:$BACKEND_PORT/api/health
- 前端:   $([[ "$START_FRONTEND" == 1 ]] && echo "http://$FRONTEND_HOST:$FRONTEND_PORT" || echo "未启动")
- vLLM:   http://$VLLM_HOST:$VLLM_PORT/v1/models
- 日志:   $LOG_DIR

按 Ctrl+C 会停止本脚本启动的服务。
DONE

while true; do
  sleep 3
  if [[ -f "$RUN_DIR/backend.pid" ]] && ! kill -0 "$(cat "$RUN_DIR/backend.pid")" >/dev/null 2>&1; then
    log "backend exited unexpectedly. 查看 logs/backend.log"
    exit 1
  fi
  if [[ -f "$RUN_DIR/vllm.pid" ]] && ! kill -0 "$(cat "$RUN_DIR/vllm.pid")" >/dev/null 2>&1; then
    log "vLLM exited unexpectedly. 查看 logs/vllm.log"
    exit 1
  fi
  if [[ "$START_FRONTEND" == 1 && -f "$RUN_DIR/frontend.pid" ]] && ! kill -0 "$(cat "$RUN_DIR/frontend.pid")" >/dev/null 2>&1; then
    log "frontend exited unexpectedly. 查看 logs/frontend.log"
    exit 1
  fi
done
