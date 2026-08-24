#!/bin/bash

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"

# ==================== 用户配置区 ====================
# 1. 指向本地 GGUF 模型文件的绝对路径
MODEL_PATH="${SCRIPT_DIR}/unsloth/Qwen3.8-27B-GGUF/Qwen3.8-27B-UD-Q4_K_XL.gguf"

# 2. 注册到 Ollama 内部的模型别名
SERVED_MODEL_NAME="qwen3.8-27b"

# 3. 指定使用的 GPU ID
export CUDA_DEVICE_ORDER=PCI_BUS_ID
export CUDA_VISIBLE_DEVICES=0

# 4. 服务运行 IP 与端口（OLLAMA_HOST 同时控制绑定 IP 和端口）
ServerIP="10.112.106.102"
PORT="8000"
export OLLAMA_HOST="${ServerIP}:${PORT}"

# 5. 禁止 Ollama 自动卸载显存（-1 代表永久常驻 RTX 3090 显存）
export OLLAMA_KEEP_ALIVE="-1"

# 6. 日志、PID 与临时配置文件路径
LOG_FILE="${SCRIPT_DIR}/ollama_server.log"
PID_FILE="${SCRIPT_DIR}/ollama_server.pid"
MODELFILE_PATH="${SCRIPT_DIR}/Modelfile"
# ====================================================

check_and_import_model() {
    # 检查模型是否已经在 Ollama 中注册
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
        if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
            echo "[!] Ollama 服务已经在运行中，PID: $(cat $PID_FILE)"
            exit 1
        fi

        echo "[+] 正在 RTX 3090 上启动 Ollama 服务..."
        echo "[+] 服务地址: http://$OLLAMA_HOST"
        echo "[+] 日志输出: $LOG_FILE"

        # 后台启动 Ollama 服务进程
        nohup ollama serve > "$LOG_FILE" 2>&1 &
        PID=$!
        echo $PID > "$PID_FILE"
        
        # 等待后台服务完成套接字绑定
        sleep 3

        # 检查并自动导入 GGUF 模型
        check_and_import_model

        # 预热并加载模型至显存
        echo "[+] 正在将模型预加载至 GPU 显存..."
        ollama run "$SERVED_MODEL_NAME" "" > /dev/null 2>&1

        echo "[✓] Ollama 服务已成功启动且模型就绪，PID: $PID"
        ;;

    stop)
        if [ ! -f "$PID_FILE" ]; then
            echo "[!] 找不到 PID 文件，服务可能未运行。"
            exit 1
        fi
        PID=$(cat "$PID_FILE")
        echo "[-] 正在停止 Ollama 服务 (PID: $PID)..."
        kill $PID
        
        while kill -0 $PID 2>/dev/null; do
            sleep 1
            echo -n "."
        done
        echo ""
        
        rm -f "$PID_FILE"
        echo "[✓] 服务已停止，显存已释放。"
        ;;

    restart)
        $0 stop
        sleep 2
        $0 start
        ;;

    status)
        if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
            echo "[✓] Ollama 服务正在运行，PID: $(cat $PID_FILE)"
            # 测试 OpenAI 兼容接口 (/v1/models) 是否可响应
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