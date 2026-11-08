#!/bin/bash

SCRIPT_DIR_TEST_OPENAI_CLIENT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

SERVER_IP=10.239.216.35

no_proxy=$no_proxy,${SERVER_IP}
NO_PROXY=$NO_PROXY,${SERVER_IP}

my_prompt="请用 100 字左右解释一下什么是张量并行。"

# OVMS + Qwen3.8-27b
python "${SCRIPT_DIR_TEST_OPENAI_CLIENT}/test_openai_client.py" \
    --base-url "http://${SERVER_IP}:8000/v3" \
    --model "qwen3.8-27b" \
    --prompt "$my_prompt" \
    --effort "medium"