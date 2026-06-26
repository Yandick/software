#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

usage() {
  cat <<'USAGE'
Usage:
  ./scripts/start_deploy.sh --base-cuda-devices 5,6 --embedding-cuda-devices 7

Behavior:
  Automatically stops previously started project processes before launching.

Environment aliases:
  OPS_BASE_CUDA_VISIBLE_DEVICES=5,6 OPS_EMBEDDING_CUDA_VISIBLE_DEVICES=7 ./scripts/start_deploy.sh
  BASE_CUDA_DEVICES=5,6 EMBEDDING_CUDA_DEVICES=7 ./scripts/start_deploy.sh

Arguments after the GPU flags are passed through to scripts/start_all.sh, for example:
  ./scripts/start_deploy.sh --base-cuda-devices 5 --embedding-cuda-devices 7 --no-frontend

Required:
  OPS_BASE_CUDA_VISIBLE_DEVICES      GPU(s) for vLLM/base Qwen model, e.g. 5 or 5,6
  OPS_EMBEDDING_CUDA_VISIBLE_DEVICES GPU(s) for backend Qwen3-Embedding, usually one GPU

Defaults applied by this deploy wrapper:
  OPS_ENABLE_AGENT_LLM=true          Enable real subagent LLM reviews by default
  OPS_AGENT_LLM_PARALLELISM=5        Run five role reviews concurrently
  VLLM_MAX_NUM_SEQS=16               Leave room for concurrent subagent calls
  VLLM_MAX_NUM_BATCHED_TOKENS=8192   Conservative batch token cap for 24GB cards
USAGE
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

echo "[deploy] stopping existing project processes..."
"$ROOT_DIR/scripts/stop_all.sh"

START_ALL_ARGS=()
BASE_DEVICES="${OPS_BASE_CUDA_VISIBLE_DEVICES:-${BASE_CUDA_DEVICES:-${VLLM_CUDA_VISIBLE_DEVICES:-${CUDA_VISIBLE_DEVICES:-}}}}"
EMBEDDING_DEVICES="${OPS_EMBEDDING_CUDA_VISIBLE_DEVICES:-${EMBEDDING_CUDA_DEVICES:-}}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --base-cuda-devices|--vllm-cuda-devices)
      local_flag="$1"
      shift
      if [[ $# -eq 0 ]]; then
        echo "$local_flag requires a value" >&2
        exit 2
      fi
      BASE_DEVICES="$1"
      ;;
    --embedding-cuda-devices)
      local_flag="$1"
      shift
      if [[ $# -eq 0 ]]; then
        echo "$local_flag requires a value" >&2
        exit 2
      fi
      EMBEDDING_DEVICES="$1"
      ;;
    *)
      START_ALL_ARGS+=("$1")
      ;;
  esac
  shift
done

if [[ -z "$BASE_DEVICES" || -z "$EMBEDDING_DEVICES" ]]; then
  echo "Missing GPU assignment." >&2
  echo >&2
  usage >&2
  exit 2
fi

export VLLM_CUDA_VISIBLE_DEVICES="$BASE_DEVICES"
export OPS_EMBEDDING_CUDA_VISIBLE_DEVICES="$EMBEDDING_DEVICES"

# Use smaller defaults on a single base GPU and slightly higher throughput on multi-GPU base deployments.
BASE_GPU_COUNT="$(awk -F',' '{print NF}' <<<"$BASE_DEVICES")"
if [[ "${BASE_GPU_COUNT:-1}" -le 1 ]]; then
  DEFAULT_VLLM_MAX_NUM_SEQS=8
  DEFAULT_VLLM_MAX_NUM_BATCHED_TOKENS=4096
else
  DEFAULT_VLLM_MAX_NUM_SEQS=16
  DEFAULT_VLLM_MAX_NUM_BATCHED_TOKENS=8192
fi

# Deployment defaults. Explicit caller-provided values still win.
export OPS_ENABLE_AGENT_LLM="${OPS_ENABLE_AGENT_LLM:-true}"
export OPS_AGENT_LLM_PARALLELISM="${OPS_AGENT_LLM_PARALLELISM:-5}"
export OPS_AGENT_LLM_TIMEOUT_SECONDS="${OPS_AGENT_LLM_TIMEOUT_SECONDS:-45}"
export VLLM_MAX_NUM_SEQS="${VLLM_MAX_NUM_SEQS:-$DEFAULT_VLLM_MAX_NUM_SEQS}"
export VLLM_MAX_NUM_BATCHED_TOKENS="${VLLM_MAX_NUM_BATCHED_TOKENS:-$DEFAULT_VLLM_MAX_NUM_BATCHED_TOKENS}"

echo "[deploy] base model GPUs: $VLLM_CUDA_VISIBLE_DEVICES"
echo "[deploy] embedding GPUs:  $OPS_EMBEDDING_CUDA_VISIBLE_DEVICES"
echo "[deploy] subagent LLM:    OPS_ENABLE_AGENT_LLM=$OPS_ENABLE_AGENT_LLM parallelism=$OPS_AGENT_LLM_PARALLELISM"

exec "$ROOT_DIR/scripts/start_all.sh" "${START_ALL_ARGS[@]}"
