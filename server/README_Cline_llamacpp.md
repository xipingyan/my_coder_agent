# Cline + Llama.cpp

Refer [README.md](./README.md)
This guide is for intel platform: Cline + OVMS(intel iGPU)

本地测试，大概需要24G GPU 内存。

# llama.cpp 搭建

```
sudo apt update && sudo apt install -y build-essential cmake git libcurl4-openssl-dev

git clone https://github.com/ggml-org/llama.cpp.git
cd llama.cpp

# 用 CUDA 编译，-DGGML_CUDA=ON 是关键
cmake -B build -DGGML_CUDA=ON -DLLAMA_CURL=ON -DCMAKE_BUILD_TYPE=Release
cmake --build build --config Release -j$(nproc)


./build/bin/llama-server --version


<!-- 先检查你的 Q4_K_M 文件是否包含 MTP 层： -->
cd ~/llama.cpp
python3 gguf-py/gguf_dump.py /path/to/Qwen3.8-27B-Q4_K_M.gguf | grep -i mtp

 启动 llama-server（核心参数

 MODEL=/path/to/Qwen3.8-27B-Q4_K_M.gguf
# 如果 Q4_K_M 不带 MTP，取消下面这行的注释并指向 MTP-only 文件
# MTP_MODEL=/path/to/Qwen3.8-27B-MTP-ONLY-Q4_K_M.gguf

~/llama.cpp/build/bin/llama-server \
  -m "$MODEL" \
  # -md "$MTP_MODEL" \          # 仅在不自带 MTP 时需要
  --alias qwen3.8-27b \
  --host 0.0.0.0 --port 8080 \
  -ngl 99 \
  -c 131072 \
  -ctk q5_0 -ctv q5_0 \
  --flash-attn on \
  --spec-type draft-mtp \
  --spec-draft-n-max 3 \
  --spec-draft-p-min 0.75 \
  -np 1 \
  --jinja \
  --temp 1.0 --top-p 0.95 --top-k 20 \
  --reasoning off

验证服务
curl http://localhost:8080/v1/models

```

# One click start ovms

```
manage_ovms.sh start
manage_ovms.sh stop
```

# Cline 配置

```
API Provider: OpenAI Compatible
Base URL: http://10.239.133.43:8000/v3
API Key: ovms（随便填）
Model ID: qwen3.8-27b
```