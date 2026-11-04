
source vllm-env/bin/activate
# pip install openai
# --effort: "none", "low", "medium", "high"

my_prompt="请用 100 字左右解释一下什么是张量并行。"
my_prompt="""/mnt/data_nvme1n1p1/xiping_workpath/my_coder_agent/tmp

帮我在这个位置写一个cpp的hello world的程序，使用cmake组织工程。
"""

export NO_PROXY=$NO_PROXY,10.112.106.102
export no_proxy=$NO_PROXY,10.112.106.102

python test_openai_client.py \
    --base-url "http://10.112.106.102:8000/v1" \
    --model "qwen3.8-27b" \
    --prompt "$my_prompt" \
    --effort "none"
    