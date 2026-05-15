#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../../.."
MODEL_PATH="${OPS_MODEL_PATH:-models/qwen3-1.7b}"
SERVED_NAME="${OPS_VLLM_MODEL_NAME:-qwen3-1.7b}"
HOST="${OPS_VLLM_HOST:-127.0.0.1}"
PORT="${OPS_VLLM_PORT:-8000}"
CUDA_DEVICES="${CUDA_VISIBLE_DEVICES:-0}"
VLLM_GPU_MEMORY_UTILIZATION="${VLLM_GPU_MEMORY_UTILIZATION:-0.90}"
VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-40960}"
export CUDA_VISIBLE_DEVICES="$CUDA_DEVICES"

exec vllm serve "$MODEL_PATH" \
  --served-model-name "$SERVED_NAME" \
  --host "$HOST" \
  --port "$PORT" \
  --reasoning-parser deepseek_r1 \
  --gpu-memory-utilization "$VLLM_GPU_MEMORY_UTILIZATION" \
  --max-model-len "$VLLM_MAX_MODEL_LEN"
