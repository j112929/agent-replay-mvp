#!/usr/bin/env bash
# Run inside a Vast SSH instance using vllm/vllm-openai:v0.26.0.
set -euo pipefail
repo_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_dir"
python3 -m pip install --no-cache-dir -e '.[training,cluster,storage]' 'ray[default,client]==2.58.0'
python3 -m pip check
export PYTHONUNBUFFERED=1
export PYTHONPATH="$repo_dir/sdk${PYTHONPATH:+:$PYTHONPATH}"
exec python3 deploy/vastai/gpu_smoke.py "$@"
