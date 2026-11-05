#!/bin/bash

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

# ==================== 用户配置区 ====================
# 1. 本地 OpenVINO IR 模型目录（Qwen3.5 VLM, int4）
MODEL_DIR="${SCRIPT_DIR}/OpenVINO/Qwen3.8-27B-int4-ov"

# 2. 对外暴露的模型名（Cline 里填的 Model ID）
SERVED_MODEL_NAME="qwen3.8-27b"

# 3. 服务地址与端口
# ServerIP 仅用于拼接客户端访问地址；BIND_ADDR 是 docker 端口发布绑定的本机地址
ServerIP="10.239.133.43"
BIND_ADDR="0.0.0.0"
PORT="8000"

# 4. 容器与镜像
# 注意：本模型（Qwen3.5/qwen3_5 混合架构 + MTP）要求 OpenVINO 2026.4+ 与 GenAI nightly，
# 正式版镜像 openvino/model_server:latest-gpu 是 2026.3，加载该 IR 会直接 segfault(139)。
# weekly 镜像为 2026.4.0，且已包含 Intel GPU 运行时，实测可用。
IMAGE="openvino/model_server:weekly"
MIN_OV_VERSION="2026.4"
CONTAINER_NAME="ovms-${SERVED_MODEL_NAME}"

# 5. 推理设备：Intel GPU（可改 GPU.0 / GPU.1 / CPU / "HETERO:GPU,CPU"）
TARGET_DEVICE="GPU"

# 6. 生成参数
MAX_NUM_SEQS=1              # 并发序列数，iGPU 显存/内存有限，别开太大
CACHE_SIZE=8                # KV cache 大小(GB)，0 = 动态分配
# 【重要】本模型 head_dim=256，Intel GPU 的 u8 KV cache 走 BY_CHANNEL 量化，
# paged attention block size 校验会失败(Expected 20, got 12)，加载不报错但首次推理即崩溃。
# 因此这里必须留空（使用模型默认精度）。
KV_CACHE_PRECISION=""
ENABLE_PREFIX_CACHING="true"
PIPELINE_TYPE="AUTO"        # LM / LM_CB / VLM / VLM_CB / AUTO
REASONING_PARSER="qwen3"    # 支持: qwen3 gemma4 gptoss minicpm5 lfm2
TOOL_PARSER="qwen3coder"    # 本模型 chat_template 为 <tool_call><function=..> XML 格式

# 思考模式开关（true = 开启思考，false = 关闭）
# OVMS 本身没有关闭思考的启动参数（--reasoning_parser 只负责把 <think> 内容
# 分离到 reasoning_content 字段）。这里通过改写 chat_template.jinja 的默认值实现：
# false 时生成一份 patch 后的模板并覆盖挂载到容器内 /model/chat_template.jinja。
# 客户端仍可用 {"chat_template_kwargs":{"enable_thinking":true}} 单次覆盖。
ENABLE_THINKING="true"
# 思考强度（仅 ENABLE_THINKING=true 时生效）：xhigh(模型默认) / medium / low
REASONING_EFFORT=""

# 运行时生成文件目录（存放 patch 后的 chat template）
RUNTIME_DIR="${SCRIPT_DIR}/ovms_runtime"

# 7. 编译缓存（GPU 首次编译 27B 极慢，缓存后重启显著加速）
OV_CACHE_DIR="${SCRIPT_DIR}/ovms_cache"

# 8. 日志
LOG_FILE="${SCRIPT_DIR}/ovms_server.log"
# ====================================================

OPENAI_BASE_URL="http://${ServerIP}:${PORT}/v3"

# 本机环境存在公司代理(http_proxy)，且 no_proxy 不含本机内网 IP，
# 直接 curl 会被代理拦截返回 403。所有对本服务的请求必须绕过代理。
CURL=(curl -s --noproxy '*')

# ---------- 工具函数 ----------
container_exists() {
    docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER_NAME"
}

is_running() {
    [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null)" = "true" ]
}

preflight() {
    if ! command -v docker >/dev/null 2>&1; then
        echo "[!] 未找到 docker，请先执行 ./install_docker_gpu.sh"
        exit 1
    fi

    if [ ! -d "$MODEL_DIR" ]; then
        echo "[!] 模型目录不存在: $MODEL_DIR"
        exit 1
    fi

    if [ ! -f "${MODEL_DIR}/openvino_language_model.xml" ]; then
        echo "[!] 未找到 openvino_language_model.xml，请确认模型已下载完整: $MODEL_DIR"
        exit 1
    fi

    if ! docker image inspect "$IMAGE" >/dev/null 2>&1; then
        echo "[!] 本地不存在镜像 $IMAGE，请先: docker pull $IMAGE"
        exit 1
    fi

    if [ ! -e /dev/dri/renderD128 ]; then
        echo "[!] 未检测到 /dev/dri/renderD128，Intel GPU 驱动可能未就绪。"
        exit 1
    fi

    # 版本校验：低于 2026.4 的 OpenVINO 加载本模型会 segfault
    OV_VER=$(docker run --rm "$IMAGE" --version 2>/dev/null | awk '/OpenVINO backend/{print $3}' | cut -d- -f1)
    if [ -n "$OV_VER" ]; then
        if [ "$(printf '%s\n%s\n' "$MIN_OV_VERSION" "$OV_VER" | sort -V | head -1)" != "$MIN_OV_VERSION" ]; then
            echo "[!] 镜像 OpenVINO 版本为 ${OV_VER}，低于本模型要求的 ${MIN_OV_VERSION}。"
            echo "    该模型为 EXPERIMENTAL，低版本加载会直接段错误(exit 139)。"
            echo "    请使用: docker pull openvino/model_server:weekly"
            exit 1
        fi
        echo "[+] 镜像 OpenVINO 版本: ${OV_VER} (要求 >= ${MIN_OV_VERSION})"
    fi
}

# 容器内默认用户 ovms(uid 5000) 不在 render 组，必须把宿主机 render 组 gid 加进去
render_gid() {
    stat -c "%g" /dev/dri/renderD128
}

# 根据 ENABLE_THINKING / REASONING_EFFORT 生成 patch 后的 chat template，
# 输出变量 TEMPLATE_MOUNT（为空表示不覆盖，用模型自带模板）
prepare_chat_template() {
    TEMPLATE_MOUNT=""
    local src="${MODEL_DIR}/chat_template.jinja"
    local dst="${RUNTIME_DIR}/chat_template.jinja"

    if [ "$ENABLE_THINKING" = "true" ] && [ -z "$REASONING_EFFORT" ]; then
        echo "[+] 思考模式 : 开启（模型默认模板）"
        return 0
    fi

    if [ ! -f "$src" ]; then
        echo "[!] 未找到 ${src}，无法调整思考模式。"
        exit 1
    fi

    mkdir -p "$RUNTIME_DIR"
    cp -f "$src" "$dst"

    if [ "$ENABLE_THINKING" = "false" ]; then
        # 1) 不再注入 reasoning_instructions
        sed -i "s/{%- if enable_thinking is undefined or enable_thinking is true %}/{%- if enable_thinking is defined and enable_thinking is true %}/" "$dst"
        # 2) 生成提示词尾部默认填入空的 <think></think>，让模型直接出答案
        sed -i "s/{%- if enable_thinking is defined and enable_thinking is false %}/{%- if enable_thinking is undefined or enable_thinking is false %}/" "$dst"
        echo "[+] 思考模式 : 关闭（覆盖 chat_template.jinja）"
    else
        echo "[+] 思考模式 : 开启，reasoning_effort=${REASONING_EFFORT}"
    fi

    if [ -n "$REASONING_EFFORT" ]; then
        case "$REASONING_EFFORT" in
            xhigh|medium|low) ;;
            *) echo "[!] REASONING_EFFORT 只支持 xhigh / medium / low"; exit 1 ;;
        esac
        sed -i "s/reasoning_effort|default('xhigh')/reasoning_effort|default('${REASONING_EFFORT}')/" "$dst"
    fi

    TEMPLATE_MOUNT="$dst"
}

wait_for_server() {
    echo "[+] 等待 OVMS 就绪（首次 GPU 编译 27B 模型可能需要数分钟，可另开终端 '$0 log' 观察）..."
    for i in $(seq 1 600); do
        if ! is_running; then
            CODE=$(docker inspect -f '{{.State.ExitCode}}' "$CONTAINER_NAME" 2>/dev/null)
            echo ""
            echo "[!] 容器已退出，exit code = ${CODE}"
            if [ "$CODE" = "139" ]; then
                echo "    139 = SIGSEGV。常见原因：OpenVINO 版本低于模型要求，或模型 IR 损坏。"
                echo "    可用 'dmesg -T | tail' 查看崩溃库名确认。"
            elif [ "$CODE" = "137" ]; then
                echo "    137 = OOM 被杀。请调小 CACHE_SIZE / MAX_NUM_SEQS。"
            fi
            echo "--- 最后 40 行日志 ---"
            docker logs --tail 40 "$CONTAINER_NAME" 2>&1
            return 1
        fi
        if "${CURL[@]}" -o /dev/null -w '%{http_code}' "http://${ServerIP}:${PORT}/v2/health/ready" 2>/dev/null | grep -q "200"; then
            echo ""
            echo "[✓] OVMS 服务就绪！"
            return 0
        fi
        sleep 2
        echo -n "."
    done
    echo ""
    echo "[!] 等待超时，请查看日志: $0 log"
    return 1
}

print_endpoint_info() {
    cat <<EOF

------------------------------------------------------------
[✓] OpenAI 兼容接口（注意 OVMS 前缀是 /v3，不是 /v1）
    Base URL : ${OPENAI_BASE_URL}
    Model ID : ${SERVED_MODEL_NAME}
    API Key  : 未启用鉴权，任意填写（如 ovms）

[VS Code Cline 配置]
    API Provider   : OpenAI Compatible
    Base URL       : ${OPENAI_BASE_URL}
    OpenAI API Key : ovms
    Model ID       : ${SERVED_MODEL_NAME}
    (本模型为 VLM，可勾选 Image Support / Supports Images)

[注意] 本机设置了 http_proxy，请确保 no_proxy 包含 ${ServerIP}，否则
       Cline / curl 会走公司代理并返回 403：
         export no_proxy="\${no_proxy},${ServerIP}"
         export NO_PROXY="\${NO_PROXY},${ServerIP}"
------------------------------------------------------------
EOF
}

# ---------- 主流程 ----------
case "$1" in
    start)
        preflight

        if is_running; then
            echo "[!] 容器 ${CONTAINER_NAME} 已在运行中，无需重复启动。"
            echo "    如需重新加载，请执行: $0 restart"
            "$0" status
            exit 0
        fi

        if container_exists; then
            echo "[+] 清理已退出的同名容器..."
            docker rm -f "$CONTAINER_NAME" >/dev/null
        fi

        if ss -tlnp 2>/dev/null | grep -q ":${PORT} "; then
            echo "[!] 端口 ${PORT} 已被占用，请先排查。"
            exit 1
        fi

        mkdir -p "$OV_CACHE_DIR"

        RENDER_GID="$(render_gid)"
        VIDEO_GID="$(stat -c "%g" /dev/dri/card1 2>/dev/null || echo 44)"

        echo "[+] 启动 OpenVINO Model Server (Intel GPU)"
        echo "[+] 模型目录 : $MODEL_DIR"
        echo "[+] 目标设备 : $TARGET_DEVICE (render gid=${RENDER_GID})"
        echo "[+] 编译缓存 : $OV_CACHE_DIR"
        echo "[+] 服务地址 : http://${ServerIP}:${PORT} (绑定 ${BIND_ADDR})"

        # 组装 OVMS 参数
        OVMS_ARGS=(
            --rest_port 8000
            --rest_bind_address 0.0.0.0
            --port 9000
            --grpc_bind_address 0.0.0.0
            --model_path /model
            --model_name "$SERVED_MODEL_NAME"
            --task text_generation
            --target_device "$TARGET_DEVICE"
            --pipeline_type "$PIPELINE_TYPE"
            --max_num_seqs "$MAX_NUM_SEQS"
            --cache_size "$CACHE_SIZE"
            --enable_prefix_caching "$ENABLE_PREFIX_CACHING"
            --cache_dir /opt/cache
            --log_level INFO
        )
        [ -n "$KV_CACHE_PRECISION" ] && OVMS_ARGS+=(--kv_cache_precision "$KV_CACHE_PRECISION")
        [ -n "$REASONING_PARSER" ]   && OVMS_ARGS+=(--reasoning_parser "$REASONING_PARSER")
        [ -n "$TOOL_PARSER" ]        && OVMS_ARGS+=(--tool_parser "$TOOL_PARSER")

        # 思考模式：需要时生成 patch 后的 chat template 并覆盖挂载
        prepare_chat_template
        MOUNT_ARGS=(-v "${MODEL_DIR}:/model:ro" -v "${OV_CACHE_DIR}:/opt/cache")
        [ -n "$TEMPLATE_MOUNT" ] && MOUNT_ARGS+=(-v "${TEMPLATE_MOUNT}:/model/chat_template.jinja:ro")

        # 不使用自动重启：崩溃时会无限重启并冲掉现场日志
        docker run -d \
            --name "$CONTAINER_NAME" \
            --restart no \
            --device /dev/dri \
            --group-add "$RENDER_GID" \
            --group-add "$VIDEO_GID" \
            --user "$(id -u):$(id -g)" \
            --shm-size 8g \
            "${MOUNT_ARGS[@]}" \
            -p "${BIND_ADDR}:${PORT}:8000" \
            "$IMAGE" \
            "${OVMS_ARGS[@]}" > /dev/null

        if [ $? -ne 0 ]; then
            echo "[!] 容器启动失败。"
            exit 1
        fi

        if ! wait_for_server; then
            exit 1
        fi

        print_endpoint_info
        ;;

    stop)
        if ! container_exists; then
            echo "[!] 容器 ${CONTAINER_NAME} 不存在。"
            exit 0
        fi
        echo "[-] 正在停止并移除容器 ${CONTAINER_NAME}..."
        docker rm -f "$CONTAINER_NAME" >/dev/null
        echo "[✓] 服务已停止，GPU 资源已释放。"
        ;;

    restart)
        "$0" stop
        sleep 3
        "$0" start
        ;;

    status)
        if is_running; then
            echo "[✓] 容器 ${CONTAINER_NAME} 运行中"
            docker ps --filter "name=${CONTAINER_NAME}" --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
            if "${CURL[@]}" "${OPENAI_BASE_URL}/models" | grep -q "$SERVED_MODEL_NAME"; then
                echo "[✓] 模型 ${SERVED_MODEL_NAME} 已就绪！"
                print_endpoint_info
            else
                echo "[!] 模型仍在加载 / 编译中，请稍候（$0 log 查看进度）。"
            fi
        else
            echo "[!] OVMS 服务未运行。"
        fi
        ;;

    log)
        docker logs -f --tail 200 "$CONTAINER_NAME" 2>&1 | tee -a "$LOG_FILE"
        ;;

    models)
        "${CURL[@]}" "${OPENAI_BASE_URL}/models"
        echo ""
        ;;

    test)
        echo "[+] 发送一次 OpenAI 兼容 chat/completions 测试请求..."
        "${CURL[@]}" "${OPENAI_BASE_URL}/chat/completions" \
            -H "Content-Type: application/json" \
            -d "{
                  \"model\": \"${SERVED_MODEL_NAME}\",
                  \"max_tokens\": 128,
                  \"messages\": [{\"role\": \"user\", \"content\": \"用一句话介绍你自己\"}]
                }"
        echo ""
        ;;

    info)
        print_endpoint_info
        ;;

    *)
        echo "使用方法: $0 {start|stop|restart|status|log|models|test|info}"
        exit 1
        ;;
esac
