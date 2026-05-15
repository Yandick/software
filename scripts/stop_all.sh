#!/usr/bin/env bash
set -euo pipefail
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT_DIR/.run"
for name in frontend backend vllm; do
  pid_file="$RUN_DIR/$name.pid"
  if [[ -f "$pid_file" ]]; then
    pid="$(cat "$pid_file" 2>/dev/null || true)"
    if [[ -n "$pid" ]]; then
      echo "stopping $name pid=$pid"
      pkill -TERM -s "$pid" >/dev/null 2>&1 || true
      kill -- "-$pid" >/dev/null 2>&1 || kill "$pid" >/dev/null 2>&1 || true
      sleep 1
      pkill -KILL -s "$pid" >/dev/null 2>&1 || true
    fi
    rm -f "$pid_file"
  fi
done
