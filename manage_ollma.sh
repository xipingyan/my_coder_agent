#!/bin/bash

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

# ==================== 用户配置区 ====================
MODEL_PATH="${SCRIPT_DIR}/unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q5_K_XL.gguf"
SERVED_MODEL_NAME="qwen3.8-27b"

export CUDA_DEVICE_ORDER=PCI_BUS_ID
export CUDA_VISIBLE_DEVICES=0

ServerIP="0.0.0.0"
PORT="8000"
export OLLAMA_HOST="${ServerIP}:${PORT}"
export OLLAMA_KEEP_ALIVE="-1"

LOG_FILE="${SCRIPT_DIR}/ollama_server.log"
PID_FILE="${SCRIPT_DIR}/ollama_server.pid"
MODELFILE_PATH="${SCRIPT_DIR}/Modelfile"
# ====================================================

# 核心辅助函数：精准校验服务是否真正在运行
is_running() {
    if [ -f "$PID_FILE" ]; then
        PID=$(cat "$PID_FILE")
        # 不仅检查 PID 存活，还检查该进程是否真的是 ollama serve
        if kill -0 "$PID" 2>/dev/null && ps -p "$PID" -o args= | grep -q "ollama serve"; then
            return 0
        else
            # 进程不存在或不是 ollama，说明是僵尸 PID 文件，自动清理
            rm -f "$PID_FILE"
        fi
    fi
    return 1
}

check_and_import_model() {
    if ! ollama list 2>/dev/null | grep -q "$SERVED_MODEL_NAME"; then
        echo "[+] 正在为 GGUF 模型生成 Modelfile 并导入 Ollama..."
        cat << EOF > "$MODELFILE_PATH"
FROM ${MODEL_PATH}
PARAMETER num_ctx 40960
EOF
        ollama create "$SERVED_MODEL_NAME" -f "$MODELFILE_PATH"
        if [ $? -eq 0 ]; then
            echo "[✓] 模型 ${SERVED_MODEL_NAME} 导入成功！"
        else
            echo "[!] 模型导入失败，请检查 GGUF 文件路径是否正确。"
            exit 1
        fi
    fi
}

case "$1" in
    start)
        if is_running; then
            echo "[!] Ollama 服务已经在运行中，PID: $(cat $PID_FILE)"
            exit 1
        fi

        # 检查端口是否被占用
        if ss -tlnp 2>/dev/null | grep -q ":${PORT} " || netstat -tlnp 2>/dev/null | grep -q ":${PORT} "; then
            echo "[!] 端口 $PORT 已被占用，请先排查占用端口的进程或等待网络释放。"
            exit 1
        fi

        echo "[+] 正在 RTX 3090 上启动 Ollama 服务..."
        echo "[+] 服务地址: http://$OLLAMA_HOST"
        echo "[+] 日志输出: $LOG_FILE"

        nohup ollama serve > "$LOG_FILE" 2>&1 &
        PID=$!
        echo $PID > "$PID_FILE"
        
        sleep 3

        # 再次检查进程是否存活（防止启动瞬间崩溃）
        if ! kill -0 $PID 2>/dev/null; then
            echo "[✗] Ollama 服务启动失败！请查看日志排查原因："
            tail -n 10 "$LOG_FILE"
            rm -f "$PID_FILE"
            exit 1
        fi

        check_and_import_model

        echo "[+] 正在将模型预加载至 GPU 显存..."
        ollama run "$SERVED_MODEL_NAME" "" > /dev/null 2>&1

        echo "[✓] Ollama 服务已成功启动且模型就绪，PID: $PID"
        ;;

    stop)
        if ! is_running; then
            echo "[!] Ollama 服务未运行或已崩溃。"
            exit 0
        fi
        
        PID=$(cat "$PID_FILE")
        echo "[-] 正在停止 Ollama 服务 (PID: $PID)..."
        
        # 优雅停止，忽略进程不存在的错误
        kill "$PID" 2>/dev/null || true
        
        for i in {1..10}; do
            if ! kill -0 "$PID" 2>/dev/null; then break; fi
            sleep 1
            echo -n "."
        done
        echo ""
        
        # 10秒没死透则强杀兜底
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
            curl -s http://$OLLAMA_HOST/v1/models | grep -q "$SERVED_MODEL_NAME" && echo "[✓] API 端口正常响应！" || echo "[!] API 服务正在初始化中..."
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