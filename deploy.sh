#!/bin/bash
# 聊天机器人可重复部署脚本 (Linux)

set -Eeuo pipefail

trap 'echo "[错误] 部署在第 ${LINENO} 行失败" >&2' ERR

POSTGRES=false
VOICE=false
for arg in "$@"; do
    case "$arg" in
        --postgres) POSTGRES=true ;;
        --voice) VOICE=true ;;
        *) echo "用法: $0 [--postgres] [--voice]" >&2; exit 2 ;;
    esac
done

COMPOSE=(docker compose)
PROJECT_NAME=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' .env 2>/dev/null | tr -d '\r' | tail -n 1)
if [ -n "$PROJECT_NAME" ]; then
    COMPOSE+=( -p "$PROJECT_NAME" )
fi
if [ "$POSTGRES" = true ]; then
    COMPOSE+=( -f docker-compose.yml -f docker-compose.postgres.yml )
fi
if [ "$VOICE" = true ]; then
    export CHATBOT_INSTALL_VOICE=true
    export CHATBOT_VOICE_ENABLED=true
    export CHATBOT_MEMORY_LIMIT="${CHATBOT_MEMORY_LIMIT:-2G}"
fi

echo "========================================="
echo "  聊天机器人部署脚本"
echo "  QQ + Telegram + Web/微信渠道"
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
    echo "[提示] 云端模型请在 .env 填入 CHATBOT_LLM_API_KEY；Ollama 模式无需密钥"
    echo "  vim .env"
    exit 1
fi

# 检查 API Key
if ! grep -Eq "provider:[[:space:]]*['\"]?ollama" config.yaml && grep -Eq "your-api-key-here|sk-xxxxx" .env; then
    echo "[错误] 请在 .env 中填入真实的 CHATBOT_LLM_API_KEY"
    echo "  vim .env"
    exit 1
fi

# 非本地监听必须 fail closed；不要部署无认证的管理端。
WEB_AUTH_TOKEN=$(sed -n 's/^CHATBOT_WEB_AUTH_TOKEN=//p' .env | tr -d '\r' | tail -n 1)
if [ "${#WEB_AUTH_TOKEN}" -lt 16 ]; then
    echo "[错误] 请在 .env 中设置至少 16 位的 CHATBOT_WEB_AUTH_TOKEN"
    exit 1
fi

# 与已有项目共存时，提前提示端口占用
if command -v ss >/dev/null 2>&1; then
    for spec in CHATBOT_WEB_PORT:8080 CHATBOT_ONEBOT_PORT:3001 NAPCAT_WEBUI_PORT:6099; do
        name="${spec%%:*}"
        default_port="${spec##*:}"
        value=$(sed -n "s/^${name}=//p" .env | tr -d '\r' | tail -n 1)
        port="${value:-$default_port}"
        if ss -lnt 2>/dev/null | awk '{print $4}' | grep -Eq "[:.]${port}$"; then
            echo "[警告] 端口 ${port} 已被占用，请修改 .env 中的 ${name}"
        fi
    done
fi

# 创建数据目录，并确保非 root 容器用户可写持久化目录
CHATBOT_UID="${CHATBOT_UID:-1000}"
CHATBOT_GID="${CHATBOT_GID:-1000}"
mkdir -p data/{logs,chroma,learning,napcat,napcat-config,media,media/tts,media/outbound,hf,whisper,tmp,home,cache}
mkdir -p characters/stickers
chown -R "${CHATBOT_UID}:${CHATBOT_GID}" data characters

# 在变更任何运行容器前先验证最终 Compose 配置。
"${COMPOSE[@]}" config --quiet

echo "[构建] 构建新镜像..."
"${COMPOSE[@]}" build chatbot

if [ "$POSTGRES" = true ]; then
    echo "[数据库] 启动 PostgreSQL 并等待健康检查..."
    "${COMPOSE[@]}" up -d postgres
fi

if [ "$VOICE" = true ]; then
    echo "[语音] 预下载 Whisper 模型（失败只降级语音，不阻塞部署）..."
    "${COMPOSE[@]}" run --rm --no-deps -e CHATBOT_VOICE_ENABLED=true chatbot \
        python -c "from faster_whisper import WhisperModel; WhisperModel('base', device='cpu', compute_type='int8')" \
        || echo "[警告] Whisper 模型预热失败，语音将在首次使用时重试"
fi

# migration job 与 runtime 使用同一镜像和配置。失败时 set -e 会阻止新版本接流量。
echo "[数据库] 执行一次性迁移..."
"${COMPOSE[@]}" run --rm --no-deps chatbot \
    python -m database.migrate --migration-timeout 300

echo "[启动] 启动所有服务..."
"${COMPOSE[@]}" up -d

echo "[等待] 检查应用就绪状态..."
READY=false
for _attempt in $(seq 1 30); do
    if "${COMPOSE[@]}" exec -T chatbot python -c \
        "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/api/v1/health/ready', timeout=3)" \
        >/dev/null 2>&1; then
        READY=true
        break
    fi
    sleep 2
done

if [ "$READY" != true ]; then
    echo "[错误] 应用未在 60 秒内通过就绪检查" >&2
    "${COMPOSE[@]}" logs --tail=100 chatbot >&2 || true
    exit 1
fi

echo ""
echo "========================================="
echo "  部署完成！"
echo "========================================="
echo ""
NAPCAT_WEBUI_PORT=$(sed -n 's/^NAPCAT_WEBUI_PORT=//p' .env | tr -d '\r' | tail -n 1)
CHATBOT_WEB_PORT=$(sed -n 's/^CHATBOT_WEB_PORT=//p' .env | tr -d '\r' | tail -n 1)
echo "  🐧 QQ登录:     http://127.0.0.1:${NAPCAT_WEBUI_PORT:-6099}（远程请使用 SSH 隧道）"
echo "  🤖 管理面板:   http://127.0.0.1:${CHATBOT_WEB_PORT:-8080}（公网请配置 TLS 反向代理）"
echo ""
echo "  查看日志: ${COMPOSE[*]} logs -f chatbot"
echo "  停止服务: ${COMPOSE[*]} down"
echo "  重启服务: ${COMPOSE[*]} restart chatbot"
echo ""
echo "  ⚠️  首次使用需要:"
echo "  1. 访问QQ登录页面扫码登录QQ"
echo "  2. 在 .env 中配置 CHATBOT_TELEGRAM_TOKEN"
echo "========================================="
