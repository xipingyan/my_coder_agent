
source vllm-env/bin/activate
# pip install openai
# --effort: "none", "low", "medium", "high"

my_prompt="请用 100 字左右解释一下什么是张量并行。"
my_prompt="""/mnt/data_nvme1n1p1/xiping_workpath/my_coder_agent/tmp

帮我在这个位置写一个cpp的hello world的程序，使用cmake组织工程。
"""

# 返回: 成功时 stdout 输出地址, exit 0; 失败时 exit 1
# 依赖: iproute2 (ip 命令), 必要时回退到 hostname
get_host_ip() {
    local family=4
    local iface=""
    local all=0

    # ---- 解析参数 ----
    while (($#)); do
        case "$1" in
            -4) family=4 ;;
            -6) family=6 ;;
            -a|--all) all=1 ;;
            -i|--interface)
                [[ -n "${2:-}" ]] || { echo "get_host_ip: -i 需要网卡名" >&2; return 1; }
                iface="$2"; shift ;;
            -h|--help)
                sed -n '2,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
                return 0 ;;
            *)
                echo "get_host_ip: 未知参数 '$1'" >&2
                return 1 ;;
        esac
        shift
    done

    # ---- 检查 ip 命令 ----
    if ! command -v ip >/dev/null 2>&1; then
        echo "get_host_ip: 未找到 ip 命令 (请安装 iproute2)" >&2
        return 1
    fi

    local flag="-${family}"

    # ---- 模式 1: 列出所有非回环地址 ----
    if (( all )); then
        ip "$flag" -o addr show ${iface:+dev "$iface"} 2>/dev/null \
            | awk '$4 != "lo" && $2 != "lo" { sub(/\/.*/, "", $4); print $4 }'
        return 0
    fi

    # ---- 模式 2: 指定网卡 ----
    if [[ -n "$iface" ]]; then
        local addr
        addr=$(ip "$flag" -o addr show dev "$iface" 2>/dev/null \
               | awk 'NR==1 { sub(/\/.*/, "", $4); print $4 }')
        if [[ -z "$addr" ]]; then
            echo "get_host_ip: 网卡 '$iface' 上没有 IPv${family} 地址" >&2
            return 1
        fi
        printf '%s\n' "$addr"
        return 0
    fi

    # ---- 模式 3: 默认路由出口地址 ----
    local target src
    if (( family == 6 )); then
        target="2001:4860:4860::8888"   # Google DNS v6, 仅用于触发路由查询
    else
        target="1.1.1.1"                # Cloudflare DNS, 仅用于触发路由查询
    fi

    # ip route get 不会真正发包, 只查路由表, 取 "src" 后的字段
    src=$(ip "$flag" route get "$target" 2>/dev/null \
          | awk '{ for (i=1;i<NF;i++) if ($i=="src") { print $(i+1); exit } }')

    if [[ -n "$src" ]]; then
        printf '%s\n' "$src"
        return 0
    fi

    # ---- 回退 1: hostname -I ----
    if command -v hostname >/dev/null 2>&1; then
        local hn
        hn=$(hostname -I 2>/dev/null)
        if [[ -n "$hn" ]]; then
            if (( family == 6 )); then
                src=$(printf '%s\n' $hn | grep -E ':' | head -n1)
            else
                src=$(printf '%s\n' $hn | grep -vE ':' | head -n1)
            fi
            if [[ -n "$src" ]]; then
                printf '%s\n' "$src"
                return 0
            fi
        fi
    fi

    # ---- 回退 2: 扫所有地址取第一个非回环 ----
    src=$(ip "$flag" -o addr show 2>/dev/null \
          | awk '$2 != "lo" { sub(/\/.*/, "", $4); print $4; exit }')
    if [[ -n "$src" ]]; then
        printf '%s\n' "$src"
        return 0
    fi

    echo "get_host_ip: 未找到 IPv${family} 地址" >&2
    return 1
}

SERVER_IP=$(get_host_ip)
# SERVER_IP=10.112.106.102

export NO_PROXY=$NO_PROXY,${SERVER_IP}
export no_proxy=$NO_PROXY,${SERVER_IP}

echo "Current server ip: ${SERVER_IP}"

python test_openai_client.py \
    --base-url "http://${SERVER_IP}:8000/v1" \
    --model "qwen3.8-27b" \
    --prompt "$my_prompt" \
    --effort "medium"
    