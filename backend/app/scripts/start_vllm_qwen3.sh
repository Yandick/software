#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
load_env_defaults() {
  local env_file=".env" line key value
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
MODEL_PATH="${OPS_MODEL_PATH:-models/qwen3-1.7b}"
SERVED_NAME="${OPS_VLLM_MODEL_NAME:-qwen3-1.7b}"
HOST="${OPS_VLLM_HOST:-127.0.0.1}"
PORT="${OPS_VLLM_PORT:-8000}"
CUDA_DEVICES="${VLLM_CUDA_VISIBLE_DEVICES:-${CUDA_VISIBLE_DEVICES:-0}}"
VLLM_REASONING_PARSER="${VLLM_REASONING_PARSER:-qwen3}"
VLLM_GPU_MEMORY_UTILIZATION="${VLLM_GPU_MEMORY_UTILIZATION:-}"
VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-40960}"
NCCL_IB_DISABLE="${NCCL_IB_DISABLE:-1}"
NCCL_NET="${NCCL_NET:-Socket}"
NCCL_DEBUG="${NCCL_DEBUG:-WARN}"
count_cuda_devices() {
  local value="$1"
  if [[ -z "$value" ]]; then
    echo 1
    return
  fi
  awk -F',' '{print NF}' <<<"$value"
}
model_params_billion() {
  python - "$MODEL_PATH" <<'PY'
import re
import sys
from pathlib import Path
name = Path(sys.argv[1]).name.lower()
match = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*b\b", name.replace("-", " "))
if not match:
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)b", name)
print(match.group(1) if match else "0")
PY
}
auto_vllm_gpu_memory_utilization() {
  python - "$1" "$2" "$3" <<'PY'
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
VLLM_TENSOR_PARALLEL_SIZE="${VLLM_TENSOR_PARALLEL_SIZE:-$(count_cuda_devices "$CUDA_DEVICES")}"
if [[ -z "$VLLM_GPU_MEMORY_UTILIZATION" ]]; then
  MODEL_PARAMS_B="$(model_params_billion)"
  VLLM_GPU_MEMORY_UTILIZATION="$(auto_vllm_gpu_memory_utilization "$MODEL_PARAMS_B" "$VLLM_MAX_MODEL_LEN" "$VLLM_TENSOR_PARALLEL_SIZE")"
fi
export CUDA_VISIBLE_DEVICES="$CUDA_DEVICES"
export NCCL_IB_DISABLE NCCL_NET NCCL_DEBUG
unset VLLM_CUDA_VISIBLE_DEVICES

args=(
  serve "$MODEL_PATH"
  --served-model-name "$SERVED_NAME" \
  --host "$HOST" \
  --port "$PORT" \
  --reasoning-parser "$VLLM_REASONING_PARSER" \
  --gpu-memory-utilization "$VLLM_GPU_MEMORY_UTILIZATION" \
  --max-model-len "$VLLM_MAX_MODEL_LEN" \
  --tensor-parallel-size "$VLLM_TENSOR_PARALLEL_SIZE"
)
if [[ -n "${VLLM_MAX_NUM_SEQS:-}" ]]; then
  args+=(--max-num-seqs "$VLLM_MAX_NUM_SEQS")
fi
if [[ -n "${VLLM_MAX_NUM_BATCHED_TOKENS:-}" ]]; then
  args+=(--max-num-batched-tokens "$VLLM_MAX_NUM_BATCHED_TOKENS")
fi

exec vllm "${args[@]}"
