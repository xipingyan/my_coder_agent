#!/bin/bash

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

# ==================== 用户配置区 ====================
MODEL_PATH="${SCRIPT_DIR}/unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q6_K.gguf"
SERVED_MODEL_NAME="qwen3.8-27b"

# 临时路径配置
export LOCAL_TMP=/mnt/data_nvme1n1p1/xiping_workpath/my_coder_agent/tmp
mkdir -p ${LOCAL_TMP}
export TMPDIR=${LOCAL_TMP}

# --- 设置显卡与 Vulkan 禁用 ---
export CUDA_VISIBLE_DEVICES=1
export OLLAMA_VULKAN=false

# 服务地址配置
ServerIP="10.112.106.102"
PORT="8000"
export OLLAMA_HOST="${ServerIP}:${PORT}"
export OLLAMA_KEEP_ALIVE="-1"

LOG_FILE="${SCRIPT_DIR}/ollama_server.log"
PID_FILE="${SCRIPT_DIR}/ollama_server.pid"
MODELFILE_PATH="${SCRIPT_DIR}/Modelfile"
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

check_and_import_model() {
    if [ ! -f "$MODEL_PATH" ]; then
        echo "[!] 错误：GGUF 模型文件不存在，请检查路径: $MODEL_PATH"
        exit 1
    fi

    # 修复：移除错误的 CLIENT_HOST，确保继承外部全局 OLLAMA_HOST
    echo "[+] 正在针对当前 GGUF 文件生成 Modelfile (num_ctx 40960) 并导入..."
    cat << EOF > "$MODELFILE_PATH"
FROM ${MODEL_PATH}
PARAMETER num_ctx 131072
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

        check_and_import_model
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