#!/bin/bash

SCRIPT_DIR_START_VLLM="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
source "${SCRIPT_DIR_START_VLLM}/vllm-env/bin/activate"

# ==================== 用户配置区 ====================
# 1. 指向本地解压/下载好的模型绝对路径
MODEL_PATH="${SCRIPT_DIR_START_VLLM}/Qwen/Qwen2.5-Coder-32B-Instruct-AWQ"

# 2. 指定使用的 GPU (确认 3090 的编号)
export CUDA_DEVICE_ORDER=PCI_BUS_ID
export CUDA_VISIBLE_DEVICES=1  # 替换为你的 3090 实际 GPU ID

# 再次启动你的 vLLM 启动脚本

# 3. 服务运行端口与服务别名
PORT=8000
ServerIP=10.112.106.102
SERVED_MODEL_NAME="qwen-coder-32b"

# 4. 日志与 PID 文件路径（修正：绑定到脚本所在目录）
LOG_FILE="${SCRIPT_DIR_START_VLLM}/vllm_server.log"
PID_FILE="${SCRIPT_DIR_START_VLLM}/vllm_server.pid"
# ====================================================

case "$1" in
    start)
        if [ -f "$PID_FILE" ] && kill -0 $(cat "$PID_FILE") 2>/dev/null; then
            echo "[!] vLLM 服务已经在运行中，PID: $(cat $PID_FILE)"
            exit 1
        fi

        echo "[+] 正在在 RTX 3090 上启动 vLLM 服务..."
        echo "[+] 模型路径: $MODEL_PATH"
        echo "[+] 日志输出: $LOG_FILE"

        # 启动 vLLM 后台进程
        nohup python3 -m vllm.entrypoints.openai.api_server \
            --model "$MODEL_PATH" \
            --quantization awq \
            --port $PORT \
            --host $ServerIP \
            --served-model-name "$SERVED_MODEL_NAME" \
            --max-model-len 16384 \
            --kv-cache-dtype fp8 \
            --enable-prefix-caching \
            --gpu-memory-utilization 0.95 \
            --max-num-seqs 4 \
            --dtype auto > "$LOG_FILE" 2>&1 &

        PID=$!
        echo $PID > "$PID_FILE"
        echo "[✓] vLLM 服务已启动，PID: $PID"
        echo "[+] 正在等待服务就绪，可运行 '$0 log' 查看加载进度..."
        ;;

    stop)
        if [ ! -f "$PID_FILE" ]; then
            echo "[!] 找不到 PID 文件，服务可能未运行。"
            exit 1
        fi
        PID=$(cat "$PID_FILE")
        echo "[-] 正在停止 vLLM 服务 (PID: $PID)..."
        kill $PID
        
        # 等待进程完全退出并释放显存
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
            echo "[✓] vLLM 服务正在运行，PID: $(cat $PID_FILE)"
            # 测试 API 是否在线
            curl -s http://$ServerIP:$PORT/v1/models | grep -q "$SERVED_MODEL_NAME" && echo "[✓] API 端口正常响应！" || echo "[!] API 服务正在初始化/加载模型中..."
        else
            echo "[!] vLLM 服务未运行。"
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