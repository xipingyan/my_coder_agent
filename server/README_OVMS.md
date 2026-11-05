# Cline + OVMS

Refer [README.md](./README.md)
This guide is for intel platform: Cline + OVMS(intel iGPU)

本地测试，大概需要24G GPU 内存。

# How to setup

```
python3 -m venv vllm-env
source vllm-env/bin/activate

docker pull openvino/model_server:weekly

<!-- Download model OpenVINO/Qwen3.8-27B-int4-ov -->
./download_model.sh

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