#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOG_DIR="$ROOT_DIR/logs"
RUN_DIR="$ROOT_DIR/.run"
mkdir -p "$LOG_DIR" "$RUN_DIR"

BACKEND_HOST="${BACKEND_HOST:-127.0.0.1}"
BACKEND_PORT="${BACKEND_PORT:-8010}"
FRONTEND_HOST="${FRONTEND_HOST:-127.0.0.1}"
FRONTEND_PORT="${FRONTEND_PORT:-5666}"
VLLM_HOST="${OPS_VLLM_HOST:-127.0.0.1}"
VLLM_PORT="${OPS_VLLM_PORT:-8000}"
MODEL_PATH="${OPS_MODEL_PATH:-models/qwen3-1.7b}"
SERVED_MODEL_NAME="${OPS_VLLM_MODEL_NAME:-qwen3-1.7b}"
CONDA_ENV="${OPS_CONDA_ENV:-${CONDA_DEFAULT_ENV:-}}"
PYTHON_BIN="${PYTHON_BIN:-python}"
PNPM_VERSION="${PNPM_VERSION:-10.33.0}"
CUDA_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"

START_LLM=1
START_FRONTEND=1
INSTALL_FRONTEND=0
STRICT_LLM=1
VLLM_TIMEOUT="${VLLM_TIMEOUT:-420}"
BACKEND_TIMEOUT="${BACKEND_TIMEOUT:-90}"
VLLM_GPU_MEMORY_UTILIZATION="${VLLM_GPU_MEMORY_UTILIZATION:-0.90}"
VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-40960}"

usage() {
  cat <<EOF
Usage: scripts/start_all.sh [options]

Options:
  --no-llm              不启动 vLLM，后端使用 OPS_INFERENCE_BACKEND=retrieval，适合快速业务测试
  --no-frontend         只启动 vLLM + 后端
  --install-frontend    启动前执行 pnpm install
  --allow-no-llm        vLLM 启动失败时不中断，后端自动使用 retrieval 兜底
  --cuda-devices VALUE  设置 CUDA_VISIBLE_DEVICES，例如 0、1、0,1；默认 0
  -h, --help            显示帮助

Env:
  OPS_CONDA_ENV         指定 Python conda 环境；默认使用当前 CONDA_DEFAULT_ENV，未设置则直接用 python
  BACKEND_PORT          后端端口，默认 8010
  FRONTEND_PORT         前端端口，默认 5666
  CUDA_VISIBLE_DEVICES  指定可见 GPU；脚本默认设为 0，建议使用者显式设置
  OPS_MODEL_PATH        模型路径，默认 models/qwen3-1.7b
  OPS_VLLM_MODEL_NAME   vLLM served model name，默认 qwen3-1.7b
  VLLM_TIMEOUT          等待 vLLM 秒数，默认 420
  VLLM_GPU_MEMORY_UTILIZATION  vLLM 显存利用率，默认 0.90
  VLLM_MAX_MODEL_LEN   vLLM 最大上下文长度，默认 40960；显存紧张可设为 8192/16384
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-llm) START_LLM=0; STRICT_LLM=0 ;;
    --no-frontend) START_FRONTEND=0 ;;
    --install-frontend) INSTALL_FRONTEND=1 ;;
    --allow-no-llm) STRICT_LLM=0 ;;
    --cuda-devices)
      shift
      if [[ $# -eq 0 ]]; then echo "--cuda-devices requires a value"; exit 2; fi
      CUDA_DEVICES="$1"
      ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1"; usage; exit 2 ;;
  esac
  shift
done

log() { printf '[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }

have_cmd() { command -v "$1" >/dev/null 2>&1; }

python_cmd() {
  if [[ -n "$CONDA_ENV" ]] && have_cmd conda; then
    conda run -n "$CONDA_ENV" "$PYTHON_BIN" "$@"
  else
    "$PYTHON_BIN" "$@"
  fi
}

run_python_bg() {
  local name="$1"; shift
  local quoted_args
  printf -v quoted_args ' %q' "$@"
  if [[ -n "$CONDA_ENV" ]] && have_cmd conda; then
    setsid bash -c "cd '$ROOT_DIR' && exec conda run --no-capture-output -n '$CONDA_ENV' '$PYTHON_BIN'$quoted_args" >"$LOG_DIR/$name.log" 2>&1 &
  else
    setsid bash -c "cd '$ROOT_DIR' && exec '$PYTHON_BIN'$quoted_args" >"$LOG_DIR/$name.log" 2>&1 &
  fi
  echo $! >"$RUN_DIR/$name.pid"
  log "$name started, pid=$(cat "$RUN_DIR/$name.pid"), log=logs/$name.log"
}

stop_pid_file() {
  local pid_file="$1" pid
  if [[ ! -f "$pid_file" ]]; then
    return 0
  fi
  pid="$(cat "$pid_file" 2>/dev/null || true)"
  if [[ -n "$pid" ]]; then
    # Services are started with setsid; stop the whole session/process group.
    pkill -TERM -s "$pid" >/dev/null 2>&1 || true
    kill -- "-$pid" >/dev/null 2>&1 || kill "$pid" >/dev/null 2>&1 || true
    sleep 1
    pkill -KILL -s "$pid" >/dev/null 2>&1 || true
  fi
  rm -f "$pid_file"
}

pnpm_cmd() {
  if [[ -n "${PNPM_CMD:-}" ]]; then
    "$PNPM_CMD" "$@"
  elif have_cmd pnpm; then
    pnpm "$@"
  elif have_cmd corepack; then
    corepack pnpm "$@"
  elif have_cmd npm; then
    npm exec --yes "pnpm@$PNPM_VERSION" -- "$@"
  else
    log "pnpm/corepack/npm 都不可用，无法启动前端"
    return 127
  fi
}

CLEANED_UP=0
cleanup() {
  if [[ "$CLEANED_UP" == 1 ]]; then
    return
  fi
  CLEANED_UP=1
  log "stopping services..."
  for name in frontend backend vllm; do
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

check_port_free() {
  local port="$1" label="$2"
  if command -v ss >/dev/null 2>&1 && ss -ltn "sport = :$port" | grep -q ":$port"; then
    log "$label port $port is already in use. 请先释放端口或修改环境变量。"
    exit 1
  fi
}

first_cuda_device() {
  local value="$1"
  value="${value%%,*}"
  value="${value//[[:space:]]/}"
  echo "$value"
}

check_gpu_memory() {
  if [[ "$START_LLM" != 1 ]]; then
    return 0
  fi
  if ! command -v nvidia-smi >/dev/null 2>&1; then
    log "nvidia-smi 不可用，跳过显存预检查。"
    return 0
  fi
  local gpu_id free_mib total_mib used_mib required_mib
  gpu_id="$(first_cuda_device "$CUDA_VISIBLE_DEVICES")"
  if [[ ! "$gpu_id" =~ ^[0-9]+$ ]]; then
    log "CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES 不是纯 GPU 编号，跳过显存预检查。"
    return 0
  fi
  local line
  line="$(nvidia-smi --query-gpu=index,memory.total,memory.used,memory.free --format=csv,noheader,nounits | awk -F', ' -v id="$gpu_id" '$1 == id {print $0}')"
  if [[ -z "$line" ]]; then
    log "未找到 GPU $gpu_id，请检查 CUDA_VISIBLE_DEVICES。"
    exit 1
  fi
  IFS=', ' read -r _ total_mib used_mib free_mib <<<"$line"
  required_mib="$(python - <<PY2
print(int(float('$total_mib') * float('$VLLM_GPU_MEMORY_UTILIZATION')))
PY2
)"
  log "GPU $gpu_id memory: total=${total_mib}MiB used=${used_mib}MiB free=${free_mib}MiB; vLLM target=${required_mib}MiB"
  if (( free_mib < required_mib )); then
    log "GPU $gpu_id 空闲显存不足以按 VLLM_GPU_MEMORY_UTILIZATION=$VLLM_GPU_MEMORY_UTILIZATION 启动 vLLM。"
    log "请先运行 nvidia-smi 选择空闲 GPU，例如：CUDA_VISIBLE_DEVICES=6 ./scripts/start_all.sh"
    log "如果必须使用当前 GPU，可降低上下文或显存比例，例如：VLLM_MAX_MODEL_LEN=8192 VLLM_GPU_MEMORY_UTILIZATION=0.25 CUDA_VISIBLE_DEVICES=$gpu_id ./scripts/start_all.sh"
    exit 1
  fi
}

cd "$ROOT_DIR"
export CUDA_VISIBLE_DEVICES="$CUDA_DEVICES"
export OPS_CUDA_VISIBLE_DEVICES="$CUDA_DEVICES"
log "project root: $ROOT_DIR"
log "python env: ${CONDA_ENV:-direct python}"
log "CUDA_VISIBLE_DEVICES=$CUDA_VISIBLE_DEVICES"
log "vLLM memory config: gpu_memory_utilization=$VLLM_GPU_MEMORY_UTILIZATION, max_model_len=$VLLM_MAX_MODEL_LEN"
check_port_free "$BACKEND_PORT" backend
if [[ "$START_FRONTEND" == 1 ]]; then check_port_free "$FRONTEND_PORT" frontend; fi
if [[ "$START_LLM" == 1 ]]; then check_port_free "$VLLM_PORT" vLLM; fi
check_gpu_memory

if [[ "$START_LLM" == 1 ]]; then
  log "starting vLLM for Qwen3: $MODEL_PATH"
  run_python_bg vllm -m vllm.entrypoints.openai.api_server \
    --model "$MODEL_PATH" \
    --served-model-name "$SERVED_MODEL_NAME" \
    --host "$VLLM_HOST" \
    --port "$VLLM_PORT" \
    --reasoning-parser deepseek_r1 \
    --gpu-memory-utilization "$VLLM_GPU_MEMORY_UTILIZATION" \
    --max-model-len "$VLLM_MAX_MODEL_LEN"
  if wait_http "http://$VLLM_HOST:$VLLM_PORT/v1/models" "$VLLM_TIMEOUT" vllm "$RUN_DIR/vllm.pid"; then
    export OPS_INFERENCE_BACKEND="${OPS_INFERENCE_BACKEND:-vllm}"
    export OPS_VLLM_BASE_URL="http://$VLLM_HOST:$VLLM_PORT/v1"
    export OPS_VLLM_MODEL_NAME="$SERVED_MODEL_NAME"
  elif [[ "$STRICT_LLM" == 1 ]]; then
    log "vLLM 启动失败，查看 logs/vllm.log。可用 --allow-no-llm 或 --no-llm 进行纯业务测试。"
    exit 1
  else
    log "vLLM 不可用，后端切换为 retrieval 兜底模式。"
    if [[ -f "$RUN_DIR/vllm.pid" ]]; then
      vllm_pid="$(cat "$RUN_DIR/vllm.pid" 2>/dev/null || true)"
      if [[ -n "$vllm_pid" ]] && kill -0 "$vllm_pid" >/dev/null 2>&1; then
        pkill -TERM -s "$vllm_pid" >/dev/null 2>&1 || true
        kill -- "-$vllm_pid" >/dev/null 2>&1 || kill "$vllm_pid" >/dev/null 2>&1 || true
      fi
      rm -f "$RUN_DIR/vllm.pid"
    fi
    START_LLM=0
    export OPS_INFERENCE_BACKEND="retrieval"
  fi
else
  export OPS_INFERENCE_BACKEND="retrieval"
  log "skip vLLM; backend inference mode: retrieval"
fi

log "starting backend: http://$BACKEND_HOST:$BACKEND_PORT"
run_python_bg backend -m uvicorn backend.app.main:app --host "$BACKEND_HOST" --port "$BACKEND_PORT"
wait_http "http://$BACKEND_HOST:$BACKEND_PORT/api/health" "$BACKEND_TIMEOUT" backend "$RUN_DIR/backend.pid" || exit 1

if [[ "$START_FRONTEND" == 1 ]]; then
  if [[ "$INSTALL_FRONTEND" == 1 || ! -d "$ROOT_DIR/frontend/node_modules" ]]; then
    log "installing frontend dependencies with pnpm..."
    (cd "$ROOT_DIR/frontend" && pnpm_cmd install) 2>&1 | tee "$LOG_DIR/frontend-install.log"
  fi
  log "starting frontend: http://$FRONTEND_HOST:$FRONTEND_PORT"
  setsid bash -c "cd '$ROOT_DIR/frontend' && $(declare -f pnpm_cmd have_cmd log); pnpm_cmd --filter ops-employee-frontend dev --host '$FRONTEND_HOST' --port '$FRONTEND_PORT'" >"$LOG_DIR/frontend.log" 2>&1 &
  echo $! >"$RUN_DIR/frontend.pid"
  log "frontend started, pid=$(cat "$RUN_DIR/frontend.pid"), log=logs/frontend.log"
fi

cat <<EOF

启动完成：
- 后端:   http://$BACKEND_HOST:$BACKEND_PORT/api/health
- 前端:   $([[ "$START_FRONTEND" == 1 ]] && echo "http://$FRONTEND_HOST:$FRONTEND_PORT" || echo "未启动")
- vLLM:   $([[ "$START_LLM" == 1 ]] && echo "http://$VLLM_HOST:$VLLM_PORT/v1/models" || echo "未启动，后端 retrieval 模式")
- 日志:   $LOG_DIR

按 Ctrl+C 会停止本脚本启动的服务。
EOF

while true; do
  sleep 3
  for name in backend; do
    pid_file="$RUN_DIR/$name.pid"
    if [[ -f "$pid_file" ]] && ! kill -0 "$(cat "$pid_file")" >/dev/null 2>&1; then
      log "$name exited unexpectedly. 查看 logs/$name.log"
      exit 1
    fi
  done
  if [[ "$START_LLM" == 1 && -f "$RUN_DIR/vllm.pid" ]] && ! kill -0 "$(cat "$RUN_DIR/vllm.pid")" >/dev/null 2>&1; then
    log "vLLM exited unexpectedly. 查看 logs/vllm.log"
    exit 1
  fi
  if [[ "$START_FRONTEND" == 1 && -f "$RUN_DIR/frontend.pid" ]] && ! kill -0 "$(cat "$RUN_DIR/frontend.pid")" >/dev/null 2>&1; then
    log "frontend exited unexpectedly. 查看 logs/frontend.log"
    exit 1
  fi
done
