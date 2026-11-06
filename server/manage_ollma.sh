#!/bin/bash

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

# ==================== 用户配置区 ====================
# MODEL_PATH="${SCRIPT_DIR}/unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q6_K.gguf"
MODEL_PATH="${SCRIPT_DIR}/unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q4_K_M.gguf"
SERVED_MODEL_NAME="qwen3.8-27b"

# 上下文长度（改这个值会触发重新导入）
NUM_CTX="131072"

# 思考模式开关 (true: 开启思考, false: 关闭思考)
ENABLE_THINKING="false"

export OLLAMA_FLASH_ATTENTION=true

# 临时路径配置
export LOCAL_TMP=${SCRIPT_DIR}/tmp
mkdir -p ${LOCAL_TMP}
export TMPDIR=${LOCAL_TMP}

# 思考模式底层环境变量配置 (控制 llama-server 推理核心层)
if [ "$ENABLE_THINKING" = "false" ]; then
    export LLAMA_ARG_REASONING="off"
    export LLAMA_ARG_THINK_BUDGET="0"
    export LLAMA_ARG_THINK="none"
else
    unset LLAMA_ARG_REASONING
    unset LLAMA_ARG_THINK_BUDGET
    unset LLAMA_ARG_THINK
fi

# --- 设置显卡与 Vulkan 禁用 ---
export CUDA_VISIBLE_DEVICES=0
# export OLLAMA_VULKAN=false

# 服务地址配置
# ==================== 本机 IP 自动探测 ====================
# 手动覆盖（可选）：执行前 export OLLAMA_BIND_IP="192.168.1.25"
# 指定优先网卡（可选）：执行前 export OLLAMA_PREFER_IFACE="eth0"
get_host_ip() {
    local ip iface

    # 1) 用户显式指定，最高优先级
    if [ -n "${OLLAMA_BIND_IP:-}" ]; then
        printf '%s\n' "$OLLAMA_BIND_IP"
        return 0
    fi

    # 2) 用户指定了优先网卡
    if [ -n "${OLLAMA_PREFER_IFACE:-}" ] && command -v ip >/dev/null 2>&1; then
        ip=$(ip -4 -o addr show dev "$OLLAMA_PREFER_IFACE" scope global 2>/dev/null \
             | awk '{a=$4; sub(/\/.*/,"",a); print a; exit}')
        if [ -n "$ip" ]; then
            printf '%s\n' "$ip"
            return 0
        fi
    fi

    # 3) 默认出口路由的源地址（只查路由表，不会真的发包）
    if command -v ip >/dev/null 2>&1; then
        ip=$(ip -4 route get 1.1.1.1 2>/dev/null \
             | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')
        case "$ip" in
            ""|127.*|0.0.0.0|169.254.*) ;;
            *) printf '%s\n' "$ip"; return 0 ;;
        esac
    fi

    # 4) 遍历全局地址，跳过 docker/veth/br-/virbr 等虚拟网卡
    if command -v ip >/dev/null 2>&1; then
        while read -r iface ip; do
            case "$iface" in
                docker*|veth*|br-*|virbr*|cni*|flannel*|tun*|tap*|kube*) continue ;;
            esac
            case "$ip" in
                ""|127.*|169.254.*) continue ;;
            esac
            printf '%s\n' "$ip"
            return 0
        done < <(ip -4 -o addr show scope global 2>/dev/null | awk '
            { a=$4; sub(/\/.*/,"",a); print $2, a }')
    fi

    # 5) hostname -I 兜底
    if command -v hostname >/dev/null 2>&1; then
        for ip in $(hostname -I 2>/dev/null); do
            case "$ip" in
                ""|127.*|169.254.*) continue ;;
            esac
            printf '%s\n' "$ip"
            return 0
        done
    fi

    # 6) 最终兜底
    printf '127.0.0.1\n'
}
# ========================================================

ServerIP="$(get_host_ip)"
PORT="8000"
export OLLAMA_HOST="${ServerIP}:${PORT}"
export OLLAMA_KEEP_ALIVE="-1"

LOG_FILE="${SCRIPT_DIR}/ollama_server.log"
PID_FILE="${SCRIPT_DIR}/ollama_server.pid"
MODELFILE_PATH="${SCRIPT_DIR}/Modelfile"
MODEL_FINGERPRINT_CACHE="${SCRIPT_DIR}/.ollama_model_fingerprint"
# ====================================================

# 精准校验服务进程
is_running() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        if kill -0 "$PID" 2>/dev/null && ps -p "$PID" -o args= | grep -q "ollama serve"; then
            return 0
        else
            rm -f "$PID_FILE"
        fi
    fi
    return 1
}

# 等待 API 服务就绪
wait_for_server() {
    echo "[+] 等待 Ollama API 服务初始化..."
    for i in {1..15}; do
        if curl -s "http://${OLLAMA_HOST}/api/version" >/dev/null 2>&1; then
            echo "[✓] Ollama API 服务就绪！"
            return 0
        fi
        sleep 1
    done
    echo "[!] 错误：服务未能在 15 秒内响应，请检查日志: $LOG_FILE"
    return 1
}

# 判断模型是否已在 Ollama 仓库中（通过官方 CLI，跨版本/跨 OLLAMA_MODELS 稳定）
model_exists_in_ollama() {
    local target="$SERVED_MODEL_NAME"
    case "$target" in
        *:*) ;;
        *)   target="${target}:latest" ;;
    esac
    ollama list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$target"
}

# 计算"快速指纹"：不读整个 20 GB，只读头尾 1 MB
# 组合：大小 + mtime + inode + 头尾 sha256 + 模型名 + num_ctx
# 换量化 / 换文件 / 改 num_ctx / 改模型名 都会让指纹变化
compute_model_fingerprint() {
    local f="$MODEL_PATH"
    [ -f "$f" ] || { echo "MISSING"; return 1; }

    local size mtime ino head_sha tail_sha tail_start
    size=$(stat -c '%s' "$f" 2>/dev/null) || return 1
    mtime=$(stat -c '%Y' "$f" 2>/dev/null) || return 1
    ino=$(stat -c '%i' "$f" 2>/dev/null) || return 1

    head_sha=$(head -c 1048576 "$f" 2>/dev/null | sha256sum | awk '{print $1}')
    tail_start=$(( size > 1048576 ? size - 1048576 : 0 ))
    tail_sha=$(tail -c +$((tail_start + 1)) "$f" 2>/dev/null | sha256sum | awk '{print $1}')

    printf '%s|%s|%s|%s|%s|%s|%s\n' \
        "$size" "$mtime" "$ino" "$head_sha" "$tail_sha" \
        "$SERVED_MODEL_NAME" "$NUM_CTX"
}

# 判断缓存是否仍然有效
model_cache_valid() {
    # 1) Ollama 仓库里必须有这个模型
    model_exists_in_ollama || return 1

    # 2) 指纹缓存文件必须存在
    [ -f "$MODEL_FINGERPRINT_CACHE" ] || return 1

    # 3) 当前指纹必须与缓存一致
    local current cached
    current=$(compute_model_fingerprint) || return 1
    cached=$(cat "$MODEL_FINGERPRINT_CACHE" 2>/dev/null)
    [ -n "$cached" ] && [ "$current" = "$cached" ]
}

save_model_fingerprint() {
    compute_model_fingerprint > "$MODEL_FINGERPRINT_CACHE"
}

check_and_import_model() {
    if [ ! -f "$MODEL_PATH" ]; then
        echo "[!] 错误：GGUF 模型文件不存在，请检查路径: $MODEL_PATH"
        exit 1
    fi

    echo "[+] 正在针对当前 GGUF 文件生成 Modelfile (num_ctx ${NUM_CTX}) 并导入..."
    cat << EOF > "$MODELFILE_PATH"
FROM ${MODEL_PATH}
PARAMETER num_ctx ${NUM_CTX}
EOF

    ollama create "$SERVED_MODEL_NAME" -f "$MODELFILE_PATH"
    if [ $? -eq 0 ]; then
        echo "[✓] 模型 ${SERVED_MODEL_NAME} 导入/更新成功！"
    else
        echo "[!] 模型导入失败，详细信息请查看日志: cat $LOG_FILE"
        exit 1
    fi
}

warmup_model() {
    echo "[+] 正在发送 API 请求将模型预加载至 GPU 显存..."
    curl -s "http://${OLLAMA_HOST}/api/generate" -d "{
      \"model\": \"${SERVED_MODEL_NAME}\",
      \"prompt\": \"warmup\",
      \"keep_alive\": -1
    }" > /dev/null

    if [ $? -eq 0 ]; then
        echo "[✓] 模型已成功预热并常驻显存！"
    else
        echo "[!] 预热请求发送失败，请检查服务状态。"
    fi
}

case "$1" in
    start)
        if is_running; then
            echo "[!] Ollama 服务已经在运行中，PID: $(cat $PID_FILE)"
            exit 1
        fi

        if ss -tlnp 2>/dev/null | grep -q ":${PORT} " || netstat -tlnp 2>/dev/null | grep -q ":${PORT} "; then
            echo "[!] 端口 $PORT 已被占用，请先排查占用端口的进程。"
            exit 1
        fi

        # 思考模式控制环境变量 (底层 llama-server 推理核心层)
        if [ "$ENABLE_THINKING" = "false" ]; then
            echo "[+] 思考模式 : 关闭 (LLAMA_ARG_REASONING=off, LLAMA_ARG_THINK_BUDGET=0)"
            export LLAMA_ARG_REASONING="off"
            export LLAMA_ARG_THINK_BUDGET="0"
            export LLAMA_ARG_THINK="none"
        else
            echo "[+] 思考模式 : 开启 (LLAMA_ARG_REASONING=auto)"
            unset LLAMA_ARG_REASONING
            unset LLAMA_ARG_THINK_BUDGET
            unset LLAMA_ARG_THINK
        fi

        echo "[+] 正在启动 Ollama 服务 (CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES})..."
        echo "[+] 服务地址: http://$OLLAMA_HOST"
        echo "[+] 日志输出: $LOG_FILE"

        nohup ollama serve > "$LOG_FILE" 2>&1 &
        PID=$!
        echo $PID > "$PID_FILE"

        if ! wait_for_server; then
            rm -f "$PID_FILE"
            exit 1
        fi

        # 缓存命中：模型已存在且文件/参数未变 -> 跳过 GGUF 导入
        if model_cache_valid; then
            echo "[✓] 模型 ${SERVED_MODEL_NAME} 指纹未变且已存在，跳过导入（缓存命中）。"
        else
            echo "[+] 模型文件或参数有变化，重新执行 ollama create ..."
            check_and_import_model
            save_model_fingerprint
        fi

        warmup_model

        echo "[✓] Ollama 服务运行中，PID: $PID"
        ;;

    stop)
        if ! is_running; then
            echo "[!] Ollama 服务未运行。"
            exit 0
        fi

        PID=$(cat "$PID_FILE")
        echo "[-] 正在停止 Ollama 服务 (PID: $PID)..."
        kill "$PID" 2>/dev/null || true

        for i in {1..10}; do
            if ! kill -0 "$PID" 2>/dev/null; then break; fi
            sleep 1
            echo -n "."
        done
        echo ""

        if kill -0 "$PID" 2>/dev/null; then
            echo "[-] 进程未响应，正在强制终止..."
            kill -9 "$PID" 2>/dev/null || true
        fi

        rm -f "$PID_FILE"
        echo "[✓] 服务已停止，显存已释放。"
        ;;

    restart)
        $0 stop
        sleep 2
        $0 start
        ;;

    status)
        if is_running; then
            echo "[✓] Ollama 服务正在运行，PID: $(cat $PID_FILE)"
            curl -s "http://$OLLAMA_HOST/v1/models" | grep -q "$SERVED_MODEL_NAME" && echo "[✓] 模型 ${SERVED_MODEL_NAME} 已就绪！" || echo "[!] 模型尚未加载或初始化中..."
            if model_cache_valid; then
                echo "[✓] 指纹缓存：有效（下次 start 会跳过导入）"
            else
                echo "[!] 指纹缓存：缺失或失效（下次 start 会重新导入）"
            fi
        else
            echo "[!] Ollama 服务未运行。"
        fi
        ;;

    log)
        tail -f "$LOG_FILE"
        ;;

    *)
        echo "使用方法: $0 {start|stop|restart|status|log}"
        exit 1
        ;;
esac