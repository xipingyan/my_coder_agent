# my_coder_agent
Setup a local coding ENV based on open source model + vLLM.

# How to setup

```
python3 -m venv vllm-env
source vllm-env/bin/activate

# 安装 vLLM (AWQ 量化支持已内置在 vLLM 中，直接安装即可)
uv pip install vllm

<!-- OLLAMA -->
curl -fsSL https://ollama.com/install.sh | sh

<!-- Donwload model: Qwen/Qwen2.5-Coder-32B-Instruct-AWQ -->
./download_model.sh

```

# One click start

#### vLLM for Qwen/Qwen2.5-Coder-32B-Instruct-AWQ
```
<!-- 一键启动 -->
./manage_vllm.sh start

<!-- 查看加载日志 -->
./manage_vllm.sh log

<!-- 检查运行状态： -->
./manage_vllm.sh status

<!-- 停止服务： -->
./manage_vllm.sh stop
```


``Note: `` 多张显卡时，使用如下命令查看对应的显卡id.
```
nvidia-smi --query-gpu=index,name,compute_cap --format=csv
```

#### OLLAMA for unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q4_K_XL.gguf

```
manage_ollma.sh start
manage_ollma.sh stop
```

# 配置插件 Continue

1. 安装插件
在 VS Code 扩展商店搜索并安装 Continue。(Continue - open-source AI code agent)

2. 修改配置文件
按 Ctrl+Shift+P（Mac 为 Cmd+Shift+P），搜索并选择 Continue: Open config.yaml，将配置修改为：

```
name: Main Config
version: 1.0.0
schema: v1

models:
  # 1. 新增的 Ollama (Qwen3.8-27B)
  - name: "Qwen3.8-27B (Ollama)"
    provider: openai
    model: qwen3.8-27b
    apiBase: "http://10.112.106.102:8000/v1"
    apiKey: "EMPTY"
    contextLength: 40960
    completionOptions:
      temperature: 0.2
    roles:
      - chat
      - edit

  # 2. 原有的 vLLM (Qwen2.5-Coder-32B)
  - name: "Qwen2.5-Coder-32B (vLLM)"
    provider: openai
    model: qwen-coder-32b
    apiBase: "http://10.112.106.102:8000/v1"
    apiKey: "EMPTY"
    contextLength: 16384
    completionOptions:
      temperature: 0.2
    roles:
      - chat
      - edit

tabAutocompleteModel:
  name: "Qwen2.5-Coder-32B Autocomplete"
  provider: openai
  # model: qwen-coder-32b
  model: qwen3.8-27b
  apiBase: "http://10.112.106.102:8000/v1"
  apiKey: "EMPTY"
```

Note:
1: For server side, please the config.yaml to ~/.continue/config.yaml
2: Replace your IP from host to 10.112.106.102
