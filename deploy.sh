#!/bin/bash
# 聊天机器人一键部署脚本 (Linux)

set -e

echo "========================================="
echo "  聊天机器人部署脚本"
echo "  微信 + QQ 双平台"
echo "========================================="

# 检查 Docker
if ! command -v docker &> /dev/null; then
    echo "[安装] Docker..."
    curl -fsSL https://get.docker.com | sh
    systemctl enable docker
    systemctl start docker
    echo "[完成] Docker 已安装"
else
    echo "[跳过] Docker 已存在: $(docker --version)"
fi

# 检查 Docker Compose
if ! docker compose version &> /dev/null; then
    echo "[安装] Docker Compose..."
    apt-get install -y docker-compose-plugin 2>/dev/null || yum install -y docker-compose-plugin 2>/dev/null
    echo "[完成] Docker Compose 已安装"
else
    echo "[跳过] Docker Compose 已存在"
fi

# 检查 .env
if [ ! -f .env ]; then
    echo "[创建] .env 文件..."
    cp .env.example .env
    echo "[提示] 请编辑 .env 填入 DEEPSEEK_API_KEY"
    echo "  vim .env"
    exit 1
fi

# 检查 API Key
if grep -q "your-api-key-here" .env; then
    echo "[错误] 请在 .env 中填入真实的 DEEPSEEK_API_KEY"
    echo "  vim .env"
    exit 1
fi

# 创建数据目录
mkdir -p data/{logs,chroma,learning,wechat,napcat}

# 构建并启动
echo "[启动] 构建并启动所有服务..."
docker compose up -d --build

# 等待服务启动
echo "[等待] 服务启动中..."
sleep 5

# 获取服务器IP
SERVER_IP=$(hostname -I | awk '{print $1}')

echo ""
echo "========================================="
echo "  部署完成！"
echo "========================================="
echo ""
echo "  📱 微信登录:   http://${SERVER_IP}:19870"
echo "  🐧 QQ登录:     http://${SERVER_IP}:6099"
echo "  🤖 管理面板:   http://${SERVER_IP}:8080"
echo ""
echo "  查看日志: docker compose logs -f"
echo "  停止服务: docker compose down"
echo "  重启服务: docker compose restart"
echo ""
echo "  ⚠️  首次使用需要:"
echo "  1. 访问微信登录页面扫码登录微信"
echo "  2. 访问QQ登录页面扫码登录QQ"
echo "========================================="
