#!/bin/bash
# Health watchdog for the Compose deployment. Run every 5 minutes from cron.
# Optional: /etc/qqbot-watchdog.env with ALERT_WEBHOOK_URL=... (JSON {"text": ...}).
set -u

PROJECT="${QQBOT_PROJECT:-/opt/qq-chatbot}"
COMPOSE_PROJECT="${QQBOT_COMPOSE_PROJECT:-qqbot}"
WEB_URL="${QQBOT_WEB_URL:-http://127.0.0.1:18080}"
STATE_DIR="${QQBOT_STATE_DIR:-/var/lib/qqbot-watchdog}"
LOG="${QQBOT_WATCHDOG_LOG:-/var/log/qqbot-watchdog.log}"

mkdir -p "$STATE_DIR"
[ -f /etc/qqbot-watchdog.env ] && . /etc/qqbot-watchdog.env

log() {
    echo "$(date -Is) $*" >> "$LOG"
}

alert() {
    local message="$1" now last
    now=$(date +%s)
    last=$(cat "$STATE_DIR/last-alert" 2>/dev/null || echo 0)
    if [ $((now - last)) -lt 1800 ]; then
        return
    fi
    echo "$now" > "$STATE_DIR/last-alert"
    log "ALERT $message"
    if [ -n "${ALERT_TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${ALERT_TELEGRAM_CHAT_ID:-}" ]; then
        curl -fsS -m 10 \
            --data-urlencode "chat_id=${ALERT_TELEGRAM_CHAT_ID}" \
            --data-urlencode "text=${message}" \
            "https://api.telegram.org/bot${ALERT_TELEGRAM_BOT_TOKEN}/sendMessage" \
            >/dev/null 2>&1 || log "telegram delivery failed"
    elif [ -n "${ALERT_WEBHOOK_URL:-}" ]; then
        local payload
        payload=$(python3 -c 'import json,sys; print(json.dumps({"text": sys.argv[1]}))' "$message")
        curl -fsS -m 10 -H 'Content-Type: application/json' --data "$payload" \
            "$ALERT_WEBHOOK_URL" >/dev/null 2>&1 || log "webhook delivery failed"
    fi
}

token=$(grep -E '^CHATBOT_WEB_AUTH_TOKEN=' "$PROJECT/.env" 2>/dev/null | head -1 | cut -d= -f2- | tr -d '"')
healthy=0
if curl -fsS -m 10 "$WEB_URL/api/v1/health" >/dev/null 2>&1; then
    status_json=$(curl -fsS -m 10 -H "Authorization: Bearer $token" \
        "$WEB_URL/api/v1/platforms/status" 2>/dev/null || true)
    if printf '%s' "$status_json" | python3 -c '
import json, sys
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
qq = next((p for p in data.get("platforms", []) if p.get("name") == "qq"), None)
sys.exit(0 if qq and qq.get("connected") else 1)
'; then
        healthy=1
    fi
fi

if [ "$healthy" -eq 1 ]; then
    previous=$(cat "$STATE_DIR/failures" 2>/dev/null || echo 0)
    previous=${previous:-0}
    if [ "$previous" -gt 0 ]; then
        log "recovered"
        alert "qqbot: 已恢复正常"
    fi
    echo 0 > "$STATE_DIR/failures"
    exit 0
fi

failures=$(cat "$STATE_DIR/failures" 2>/dev/null || echo 0)
failures=${failures:-0}
failures=$((failures + 1))
echo "$failures" > "$STATE_DIR/failures"
log "unhealthy (consecutive=$failures)"

if [ "$failures" -lt 2 ]; then
    exit 0
fi

now=$(date +%s)
last_restart=$(cat "$STATE_DIR/last-restart" 2>/dev/null || echo 0)
if [ $((now - last_restart)) -ge 900 ]; then
    echo "$now" > "$STATE_DIR/last-restart"
    log "restarting chatbot"
    if (cd "$PROJECT" && docker compose -p "$COMPOSE_PROJECT" restart chatbot >>"$LOG" 2>&1); then
        alert "qqbot: 健康检查失败，已重启 chatbot"
    else
        alert "qqbot: 健康检查失败，重启 chatbot 也失败了"
    fi
else
    alert "qqbot: 健康检查持续失败"
fi
