#!/bin/bash
# copy_ollama_model.sh - 把已 pull 的 Ollama 模型完整复制到当前目录
# 用法: ./copy_ollama_model.sh [模型名[:tag]]
# 默认: smtek/Swift-Qwen3.8-27B:latest

set -euo pipefail

MODEL_NAME="${1:-smtek/Swift-Qwen3.8-27B:latest}"

# ---------- 解析模型名 ----------
parse_model() {
    local name="$1"
    local tag="latest"
    if [[ "$name" == *:* ]]; then
        tag="${name##*:}"
        name="${name%:*}"
    fi
    local parts=(${name//\// })
    local registry namespace model
    case ${#parts[@]} in
        1) registry="registry.ollama.ai"; namespace="library";     model="${parts[0]}" ;;
        2) registry="registry.ollama.ai"; namespace="${parts[0]}"; model="${parts[1]}" ;;
        3) registry="${parts[0]}";        namespace="${parts[1]}"; model="${parts[2]}" ;;
        *) echo "❌ 无法解析模型名: $name" >&2; exit 1 ;;
    esac
    echo "$registry|$namespace|$model|$tag"
}

IFS='|' read -r REGISTRY NAMESPACE MODEL TAG <<< "$(parse_model "$MODEL_NAME")"
echo "🔍 目标模型: $MODEL_NAME"
echo "   registry=$REGISTRY namespace=$NAMESPACE model=$MODEL tag=$TAG"

# ---------- 定位 Ollama 模型目录 ----------
OLLAMA_MODELS_DIR=""
for dir in "${OLLAMA_MODELS:-}" "$HOME/.ollama/models" "/var/lib/ollama/models" "/usr/share/ollama/.ollama/models"; do
    if [ -n "$dir" ] && [ -d "$dir/blobs" ]; then
        OLLAMA_MODELS_DIR="$dir"
        break
    fi
done
[ -z "$OLLAMA_MODELS_DIR" ] && { echo "❌ 找不到 Ollama 模型目录。" >&2; exit 1; }
echo "✅ 模型目录: $OLLAMA_MODELS_DIR"

# ---------- 定位 manifest：精确路径优先，失败则自动搜索 ----------
MANIFEST_SRC="$OLLAMA_MODELS_DIR/manifests/$REGISTRY/$NAMESPACE/$MODEL/$TAG"

if [ ! -f "$MANIFEST_SRC" ]; then
    echo "⚠️  精确路径不存在: $MANIFEST_SRC"
    echo "🔎 自动搜索 manifests 目录..."

    CANDIDATES=$(find "$OLLAMA_MODELS_DIR/manifests" -type f 2>/dev/null | sort || true)
    COUNT=$(echo "$CANDIDATES" | grep -c . || true)

    if [ "$COUNT" -eq 0 ]; then
        echo "❌ manifests 目录为空。" >&2
        exit 1
    fi

    if [ "$COUNT" -eq 1 ]; then
        MANIFEST_SRC=$(echo "$CANDIDATES" | head -1)
        echo "ℹ️  只找到 1 个 manifest，直接使用: $MANIFEST_SRC"
    else
        echo "ℹ️  找到 $COUNT 个 manifest，尝试用 ollama list 的 ID 匹配..."

        MODEL_ID=""
        if command -v ollama >/dev/null 2>&1; then
            MODEL_ID=$(ollama list 2>/dev/null | awk -v m="$MODEL_NAME" '
                $1 == m {print $2; exit}
            ')
            [ -z "$MODEL_ID" ] && MODEL_ID=$(ollama list 2>/dev/null | awk -v m="$MODEL" '
                index($1, m) == 1 {print $2; exit}
            ')
        fi

        FOUND=""
        if [ -n "$MODEL_ID" ]; then
            echo "ℹ️  模型 ID: $MODEL_ID"
            while IFS= read -r mf; do
                [ -z "$mf" ] && continue
                while IFS= read -r d; do
                    # d 形如 sha256:xxxx，取第 7-18 位与 MODEL_ID 比较
                    if [[ "${d:7:12}" == "$MODEL_ID" ]]; then
                        FOUND="$mf"
                        break
                    fi
                done < <(grep -oE 'sha256:[a-f0-9]{64}' "$mf" 2>/dev/null | sort -u)
                [ -n "$FOUND" ] && break
            done <<< "$CANDIDATES"
        fi

        if [ -z "$FOUND" ]; then
            echo "❌ 无法自动匹配 manifest。可用列表:" >&2
            echo "$CANDIDATES" | sed 's/^/     /' >&2
            exit 1
        fi
        MANIFEST_SRC="$FOUND"
        echo "✅ 匹配到 manifest: $MANIFEST_SRC"
    fi
else
    echo "✅ manifest: $MANIFEST_SRC"
fi

# ---------- 提取 digest ----------
DIGESTS=$(grep -oE 'sha256:[a-f0-9]{64}' "$MANIFEST_SRC" | sort -u)
BLOB_COUNT=$(echo "$DIGESTS" | grep -c 'sha256:' || true)
echo "📦 manifest 引用 $BLOB_COUNT 个 blob"

# ---------- 创建输出目录 ----------
OUTPUT_DIR="./"
mkdir -p "$OUTPUT_DIR/blobs"
mkdir -p "$OUTPUT_DIR/manifests/$REGISTRY/$NAMESPACE/$MODEL"
echo "📂 输出目录: $(cd "$OUTPUT_DIR" && pwd)"

# ---------- 复制 blobs ----------
COUNT=0; SKIP=0; MISSING=0
while IFS= read -r digest; do
    [ -z "$digest" ] && continue
    blob_name="sha256-${digest#sha256:}"
    SRC_FILE="$OLLAMA_MODELS_DIR/blobs/$blob_name"
    DST_FILE="$OUTPUT_DIR/blobs/$blob_name"

    if [ ! -f "$SRC_FILE" ]; then
        echo "  ⚠️  缺失: $blob_name" >&2
        MISSING=$((MISSING+1))
        continue
    fi

    SRC_SIZE=$(stat -c%s "$SRC_FILE" 2>/dev/null || stat -f%z "$SRC_FILE" 2>/dev/null || echo 0)
    if [ -f "$DST_FILE" ]; then
        DST_SIZE=$(stat -c%s "$DST_FILE" 2>/dev/null || stat -f%z "$DST_FILE" 2>/dev/null || echo -1)
        if [ "$SRC_SIZE" = "$DST_SIZE" ]; then
            echo "  ⏭  已存在: $blob_name"
            SKIP=$((SKIP+1))
            continue
        fi
    fi

    cp -a "$SRC_FILE" "$DST_FILE"
    echo "  ✅ 已复制: $blob_name ($(du -h "$SRC_FILE" 2>/dev/null | cut -f1))"
    COUNT=$((COUNT+1))
done <<< "$DIGESTS"

# ---------- 复制 manifest 到目标路径（按 $MODEL_NAME 的 tag 命名） ----------
cp -a "$MANIFEST_SRC" "$OUTPUT_DIR/manifests/$REGISTRY/$NAMESPACE/$MODEL/$TAG"
echo "  ✅ 已复制 manifest -> manifests/$REGISTRY/$NAMESPACE/$MODEL/$TAG"

# ---------- 参考 Modelfile（可选） ----------
if command -v ollama >/dev/null 2>&1; then
    if ollama show --modelfile "$MODEL_NAME" > "$OUTPUT_DIR/Modelfile.exported" 2>/dev/null; then
        echo "  ✅ 已保存参考 Modelfile: Modelfile.exported"
    else
        rm -f "$OUTPUT_DIR/Modelfile.exported"
    fi
fi

# ---------- 汇总 ----------
echo ""
if [ "$MISSING" -gt 0 ]; then
    echo "⚠️  完成，但有 $MISSING 个 blob 缺失。"
    exit 1
fi

echo "🎉 导出完成！"
echo "   新复制 blob: $COUNT"
echo "   已存在跳过: $SKIP"
echo "📁 输出目录: $(cd "$OUTPUT_DIR" && pwd)"
echo ""
echo "💡 目标机器使用:"
echo "   OLLAMA_MODELS=$(cd "$OUTPUT_DIR" && pwd) ollama serve"
echo ""
echo "⚠️  注意: 目标 tag 是 :$TAG。若启动脚本需要其它 tag（如 :map-k4v），"
echo "   可在目标机器执行:"
echo "     OLLAMA_MODELS=$(cd "$OUTPUT_DIR" && pwd) ollama cp $MODEL_NAME ${MODEL}:map-k4v"