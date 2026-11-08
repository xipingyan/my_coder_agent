#!/bin/bash

# ==================== 本机 IP 自动探测 ====================
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
