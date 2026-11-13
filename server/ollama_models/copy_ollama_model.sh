#!/bin/bash
# export_ollama_model.sh - 将指定 Ollama 模型的相关文件复制到当前目录

MODEL_NAME="${1:-smtek/Swift-Qwen3.8-27B}" # 默认模型，可通过参数覆盖

# 1. 确定 Ollama 模型基础目录 (兼容不同安装方式)
OLLAMA_DIR=""
for dir in "$HOME/.ollama" "/var/lib/ollama" "/usr/share/ollama/.ollama"; do
    if [ -d "$dir/models" ]; then
        OLLAMA_DIR="$dir"
        break
    fi
done

if [ -z "$OLLAMA_DIR" ]; then
    echo "❌ 找不到 Ollama 模型目录。请确认 Ollama 已安装并已拉取模型。"
    exit 1
fi

echo "✅ 找到 Ollama 目录: $OLLAMA_DIR"
echo "🔍 正在解析模型 '$MODEL_NAME' 的 Modelfile..."

# 2. 获取 Modelfile 并解析出 blob 哈希值
MODELFILE=$(ollama show --modelfile "$MODEL_NAME" 2>/dev/null)
if [ -z "$MODELFILE" ]; then
    echo "❌ 获取模型 '$MODEL_NAME' 的 Modelfile 失败。请确认模型名称正确。"
    exit 1
fi

# 提取所有形如 sha256-xxxxx 的字符串，并去重
HASHES=$(echo "$MODELFILE" | grep -oE 'sha256-[a-f0-9]+' | sort -u)

if [ -z "$HASHES" ]; then
    echo "❌ 未在 Modelfile 中找到任何 blob 文件引用。"
    exit 1
fi

# 3. 创建输出目录并复制文件
OUTPUT_DIR="./"
mkdir -p "$OUTPUT_DIR/blobs"
mkdir -p "$OUTPUT_DIR/manifests"

echo "📂 正在复制文件到: $OUTPUT_DIR"

COUNT=0
for hash in $HASHES; do
    SRC_FILE="$OLLAMA_DIR/models/blobs/$hash"
    if [ -f "$SRC_FILE" ]; then
        cp "$SRC_FILE" "$OUTPUT_DIR/blobs/"
        echo "  ✅ 已复制: $hash"
        ((COUNT++))
    else
        echo "  ⚠️  警告: 在 blobs 目录中找不到 $hash，可能该文件已损坏或缺失。"
    fi
done

# 4. 保存 Modelfile 本身，便于导入时参考
echo "$MODELFILE" > "$OUTPUT_DIR/Modelfile"

echo ""
echo "🎉 导出完成！共复制了 $COUNT 个 blob 文件。"
echo "📁 输出目录: $OUTPUT_DIR"
echo "💡 要将此模型导入到另一台机器，可以将整个 '$OUTPUT_DIR' 目录传输过去，"
echo "   然后在目标机器上运行: ollama create my-model -f $OUTPUT_DIR/Modelfile"
