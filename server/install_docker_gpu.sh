#!/bin/bash
# Author: AI Assistant
# Description: 一键安装 Docker 和 NVIDIA Container Toolkit (适配国内网络)

set -e

# --- 颜色定义 ---
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[0;33m'
NC='\033[0m'

# --- 检测系统发行版 ---
if [ -f /etc/os-release ]; then
    . /etc/os-release
    OS=$ID
    VERSION=$VERSION_ID
else
    echo -e "${RED}无法检测系统发行版，脚本退出。${NC}"
    exit 1
fi

# --- 仅支持 Ubuntu/Debian 和 CentOS/RHEL/Rocky ---
if [[ "$OS" != "ubuntu" && "$OS" != "debian" && "$OS" != "centos" && "$OS" != "rhel" && "$OS" != "rocky" ]]; then
    echo -e "${RED}当前脚本仅支持 Ubuntu/Debian 和 CentOS/RHEL/Rocky Linux。${NC}"
    exit 1
fi

# --- 1. 卸载旧版本 Docker ---
echo -e "${GREEN}[1/7] 卸载旧版本 Docker...${NC}"
if [[ "$OS" == "ubuntu" || "$OS" == "debian" ]]; then
    sudo apt-get remove -y docker docker-engine docker.io containerd runc 2>/dev/null || true
elif [[ "$OS" == "centos" || "$OS" == "rhel" || "$OS" == "rocky" ]]; then
    sudo yum remove -y docker docker-client docker-client-latest docker-common docker-latest docker-latest-logrotate docker-logrotate docker-engine 2>/dev/null || true
fi

# --- 2. 安装依赖 ---
echo -e "${GREEN}[2/7] 安装依赖包...${NC}"
if [[ "$OS" == "ubuntu" || "$OS" == "debian" ]]; then
    sudo apt-get update
    sudo apt-get install -y \
        apt-transport-https \
        ca-certificates \
        curl \
        gnupg \
        lsb-release \
        software-properties-common
elif [[ "$OS" == "centos" || "$OS" == "rhel" || "$OS" == "rocky" ]]; then
    sudo yum install -y yum-utils device-mapper-persistent-data lvm2
fi

# --- 3. 添加 Docker 国内源 ---
echo -e "${GREEN}[3/7] 添加 Docker 国内源...${NC}"
if [[ "$OS" == "ubuntu" || "$OS" == "debian" ]]; then
    # 使用阿里云 Docker 源[reference:10]
    curl -fsSL https://mirrors.aliyun.com/docker-ce/linux/ubuntu/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker-archive-keyring.gpg
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker-archive-keyring.gpg] https://mirrors.aliyun.com/docker-ce/linux/ubuntu $(lsb_release -cs) stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null
elif [[ "$OS" == "centos" || "$OS" == "rhel" || "$OS" == "rocky" ]]; then
    # 使用阿里云 Docker 源[reference:11]
    sudo yum-config-manager --add-repo https://mirrors.aliyun.com/docker-ce/linux/centos/docker-ce.repo
fi

# --- 4. 安装 Docker ---
echo -e "${GREEN}[4/7] 安装 Docker...${NC}"
if [[ "$OS" == "ubuntu" || "$OS" == "debian" ]]; then
    sudo apt-get update
    sudo apt-get install -y docker-ce docker-ce-cli containerd.io
elif [[ "$OS" == "centos" || "$OS" == "rhel" || "$OS" == "rocky" ]]; then
    sudo yum install -y docker-ce docker-ce-cli containerd.io
fi

# --- 5. 配置 Docker 镜像加速和 NVIDIA 运行时 ---
echo -e "${GREEN}[5/7] 配置 Docker 镜像加速器和 NVIDIA 运行时...${NC}"
sudo mkdir -p /etc/docker
sudo tee /etc/docker/daemon.json <<-'EOF'
{
  "registry-mirrors": [
    "https://docker.xuanyuan.me",
    "https://docker.1ms.run",
    "https://docker.m.daocloud.io"
  ],
  "runtimes": {
    "nvidia": {
      "args": [],
      "path": "nvidia-container-runtime"
    }
  }
}
EOF

# --- 6. 启动 Docker ---
echo -e "${GREEN}[6/7] 启动 Docker 服务...${NC}"
sudo systemctl daemon-reload
sudo systemctl restart docker
sudo systemctl enable docker

# --- 7. 安装 NVIDIA Container Toolkit ---
echo -e "${GREEN}[7/7] 安装 NVIDIA Container Toolkit...${NC}"
if [[ "$OS" == "ubuntu" || "$OS" == "debian" ]]; then
    # 使用中科大 USTC 镜像源[reference:12][reference:13]
    curl -fsSL https://mirrors.ustc.edu.cn/libnvidia-container/stable/deb/nvidia-container-toolkit.list | sudo tee /etc/apt/sources.list.d/nvidia-container-toolkit.list
    sed -i 's@https://nvidia.github.io@https://mirrors.ustc.edu.cn@' /etc/apt/sources.list.d/nvidia-container-toolkit.list
    sudo apt-get update
    sudo apt-get install -y nvidia-container-toolkit
elif [[ "$OS" == "centos" || "$OS" == "rhel" || "$OS" == "rocky" ]]; then
    # 使用腾讯云镜像源[reference:14]
    curl -s -L https://mirrors.tencentyun.com/libnvidia-container/stable/rpm/nvidia-container-toolkit.repo | sudo tee /etc/yum.repos.d/nvidia-container-toolkit.repo
    sed -i 's@nvidia.github.io@mirrors.tencentyun.com@g' /etc/yum.repos.d/nvidia-container-toolkit.repo
    sudo yum makecache
    sudo yum install -y nvidia-container-toolkit
fi

# 配置 NVIDIA Container Toolkit[reference:15]
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# --- 验证安装 ---
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}✅ Docker 和 NVIDIA Container Toolkit 安装完成！${NC}"
echo -e "${GREEN}========================================${NC}"

echo -e "${YELLOW}Docker 版本:${NC}"
docker --version

echo -e "${YELLOW}NVIDIA Container Toolkit 版本:${NC}"
nvidia-container-toolkit --version 2>/dev/null || echo "nvidia-container-toolkit 已安装"

echo -e "${YELLOW}验证 Docker 镜像加速:${NC}"
sudo docker info | grep -A 5 "Registry Mirrors"

echo -e "${YELLOW}验证 NVIDIA 运行时:${NC}"
sudo docker info | grep -A 5 "Runtimes"

echo -e "${GREEN}安装完成！${NC}"