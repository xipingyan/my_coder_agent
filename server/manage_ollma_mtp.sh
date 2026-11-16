#!/bin/bash

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

# ==================== 用户配置区 ====================
# 自包含模型库（manifest + blobs），整个目录拷到其它机器即可使用
export OLLAMA_MODELS="${SCRIPT_DIR}/ollama_models"
SERVED_MODEL_NAME="smtek/Swift-Qwen3.8-27B:map-k4v"

# 上下文长度：留空则使用模型自带的 num_ctx(143360)；设置后仅在预热请求中覆盖
# 注意：客户端请求的 num_ctx 与预热不一致会触发模型重新加载
NUM_CTX=""

# 自定义（打过补丁的）Ollama 可执行文件；stock Ollama 不会转发 draft_* 参数，MTP 不生效
OLLAMA_BIN="${OLLAMA_BIN:-ollama}"

# 思考模式开关 (true: 开启思考, false: 关闭思考)
ENABLE_THINKING="false"

export OLLAMA_FLASH_ATTENTION=true
export OLLAMA_KV_CACHE_TYPE="q8_0"

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

# --- GPU 选择 ---
# 用法: $0 start [gpu_id]，或 GPU_ID=1 $0 start；默认 0（编号与 nvidia-smi 一致）
GPU_ID="${2:-${GPU_ID:-0}}"
if ! [[ "$GPU_ID" =~ ^[0-9]+(,[0-9]+)*$ ]]; then
    echo "[!] 无效的 GPU id: $GPU_ID"
    exit 1
fi
# 默认 CUDA 按“性能最快优先”排序，编号会与 nvidia-smi 不一致，
# 导致设置 1 实际选到另一张卡；强制按 PCI 总线顺序与 nvidia-smi 对齐
export CUDA_DEVICE_ORDER=PCI_BUS_ID
export CUDA_VISIBLE_DEVICES="$GPU_ID"
# Ollama 0.32 默认同时启用 Vulkan 后端，Vulkan 不读取 CUDA_VISIBLE_DEVICES，
# 会把所有显卡都暴露出来并可能选中 V100；关闭后只用 CUDA 后端
export OLLAMA_VULKAN=false

# 根据 GPU id 获取显卡名称（用于显示）
get_gpu_names() {
    local id names="" n
    for id in ${GPU_ID//,/ }; do
        n=$(nvidia-smi --query-gpu=name --format=csv,noheader -i "$id" 2>/dev/null | head -n1)
        names+="${names:+, }${n:-未知}"
    done
    printf '%s\n' "$names"
}
GPU_NAME="$(get_gpu_names)"

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

LOG_FILE="${SCRIPT_DIR}/ollama_server_mtp.log"
PID_FILE="${SCRIPT_DIR}/ollama_server_mtp.pid"
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

# 判断模型是否已在本地模型库中
model_exists_in_ollama() {
    "$OLLAMA_BIN" list 2>/dev/null | awk 'NR>1 {print $1}' | grep -qx "$SERVED_MODEL_NAME"
}

warmup_model() {
    echo "[+] 正在发送 API 请求将模型预加载至 GPU 显存..."
    local options=""
    [ -n "$NUM_CTX" ] && options=", \"options\": {\"num_ctx\": ${NUM_CTX}}"
    curl -s "http://${OLLAMA_HOST}/api/generate" -d "{
      \"model\": \"${SERVED_MODEL_NAME}\",
      \"prompt\": \"warmup\",
      \"keep_alive\": -1${options}
    }" > /dev/null

    if [ $? -eq 0 ]; then
        echo "[✓] 模型已成功预热并常驻显存！"
    else
        echo "[!] 预热请求发送失败，请检查服务状态。"
    fi
}

# 检查日志中 llama-server 是否带有投机解码参数，用于确认 MTP 是否生效
show_mtp_state() {
    if grep -qiE 'draft-mtp|spec-type|--draft|ngram' "$LOG_FILE" 2>/dev/null; then
        echo "[✓] 日志中检测到投机解码(MTP/ngram)相关参数："
        grep -iE 'draft-mtp|spec-type|--draft|ngram' "$LOG_FILE" | tail -n 3 | cut -c1-300
    else
        echo "[!] 日志中未发现投机解码参数：当前 Ollama 可能不支持 draft_* 转发，MTP 未生效（需要补丁版 Ollama，设置 OLLAMA_BIN）。"
    fi
}

case "$1" in
    start)
        if is_running; then
            echo "[!] Ollama 服务已经在运行中，PID: $(cat $PID_FILE)"
            exit 1
        fi

        if ss -tlnp 2>/dev/null | grep -q ":${PORT} " || netstat -tlnp 2>/dev/null | grep -q ":${PORT} "; then
            echo "[!] 端口 $PORT 已被占用，请先排查占用端口的进程（例如旧的 manage_ollma.sh 服务）。"
            exit 1
        fi

        if [ ! -d "$OLLAMA_MODELS/manifests" ]; then
            echo "[!] 错误：模型库不存在: $OLLAMA_MODELS"
            exit 1
        fi

        if [ "$ENABLE_THINKING" = "false" ]; then
            echo "[+] 思考模式 : 关闭 (LLAMA_ARG_REASONING=off, LLAMA_ARG_THINK_BUDGET=0)"
        else
            echo "[+] 思考模式 : 开启"
        fi

        echo "[+] 正在启动 Ollama 服务 (CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES}, 设备: ${GPU_NAME})..."
        echo "[+] 模型库: $OLLAMA_MODELS"
        echo "[+] 服务地址: http://$OLLAMA_HOST"
        echo "[+] 日志输出: $LOG_FILE"

        nohup "$OLLAMA_BIN" serve > "$LOG_FILE" 2>&1 &
        PID=$!
        echo $PID > "$PID_FILE"

        if ! wait_for_server; then
            rm -f "$PID_FILE"
            exit 1
        fi

        if ! model_exists_in_ollama; then
            echo "[!] 错误：模型库中没有 ${SERVED_MODEL_NAME}，请检查 $OLLAMA_MODELS"
            $0 stop
            exit 1
        fi

        warmup_model
        show_mtp_state

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
        $0 start "$GPU_ID"
        ;;

    status)
        if is_running; then
            echo "[✓] Ollama 服务正在运行，PID: $(cat $PID_FILE)"
            curl -s "http://$OLLAMA_HOST/v1/models" | grep -q "$SERVED_MODEL_NAME" && echo "[✓] 模型 ${SERVED_MODEL_NAME} 已就绪！" || echo "[!] 模型尚未加载或初始化中..."
            show_mtp_state
        else
            echo "[!] Ollama 服务未运行。"
        fi
        ;;

    log)
        tail -f "$LOG_FILE"
        ;;

    *)
        echo "使用方法: $0 {start|stop|restart|status|log} [gpu_id]  (默认 gpu_id=0)"
        exit 1
        ;;
esac
