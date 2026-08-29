#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
source "$SCRIPT_DIR/vllm-env/bin/activate"

# dependcy
# pip install openvino-tokenizers openvino nncf optimum[intel]
# uv pip install --index-url https://pypi.org/simple -U huggingface-hub

# model_id='Qwen/Qwen2.5-Coder-32B-Instruct-AWQ'
# model_id='unsloth/Qwen3.8-27B-GGUF MTP Qwen3.8-27B-UD-Q5_K_XL.gguf config.json imatrix_unsloth.gguf mmproj-BF16.gguf mmproj-F16.gguf'
# model_id='bartowski/Qwen2.5-Coder-32B-Instruct-GGUF Qwen2.5-Coder-32B-Instruct-Q4_K_M.gguf'

model_id='bartowski/DeepSeek-Coder-V2-Lite-Instruct-GGUF DeepSeek-Coder-V2-Lite-Instruct-Q6_K_L.gguf'
model_id='OpenVINO/Qwen3.8-27B-int4-ov'

# Refer: https://hf-mirror.com/
export HF_ENDPOINT=https://hf-mirror.com
hf download --token [your token] $model_id --local-dir ./$model_id
