#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
source "$SCRIPT_DIR/vllm-env/bin/activate"

# dependcy
# pip install openvino-tokenizers openvino nncf optimum[intel]
# uv pip install --index-url https://pypi.org/simple -U huggingface-hub

model_id='Qwen/Qwen2.5-Coder-32B-Instruct-AWQ'

# Refer: https://hf-mirror.com/
export HF_ENDPOINT=https://hf-mirror.com
hf download --token [your token] $model_id --local-dir ./$model_id
