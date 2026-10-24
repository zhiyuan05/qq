#!/bin/bash
# Daily backup. Optional offsite copy: /etc/qqbot-backup.env with
#   BACKUP_RCLONE_REMOTE=remote:path   (requires rclone config)
#   BACKUP_RSYNC_TARGET=user@host:/path
set -uo pipefail

PROJECT="${QQBOT_PROJECT:-/opt/qq-chatbot}"
DEST="${QQBOT_BACKUP_DIR:-/opt/backups/qqbot}"
RETENTION_DAYS="${QQBOT_BACKUP_DAYS:-7}"

cd "$PROJECT"
mkdir -p "$DEST"
chmod 700 "$DEST"
stamp=$(date +%Y%m%d-%H%M)
archive="$DEST/qqbot-$stamp.tar.gz"
tar -czf "$archive" \
  --exclude="data/logs/*" --exclude="data/tmp/*" --exclude="data/cache/*" \
  --exclude="data/*.sock" --warning=no-file-changed \
  data characters .env config.yaml config/config.yaml docker-compose.yml
rc=$?
# tar exit 1 only means a live file changed while reading; the archive is still usable.
if [ "$rc" -gt 1 ]; then
    echo "$(date -Is) backup failed rc=$rc" >&2
    exit "$rc"
fi
chmod 600 "$archive"
find "$DEST" -name "qqbot-*.tar.gz" -mtime +"$RETENTION_DAYS" -delete
echo "$(date -Is) backup written: $archive"

[ -f /etc/qqbot-backup.env ] && . /etc/qqbot-backup.env

if [ -n "${BACKUP_RCLONE_REMOTE:-}" ] && command -v rclone >/dev/null 2>&1; then
    if timeout 3600 rclone copy "$archive" "$BACKUP_RCLONE_REMOTE" >>/var/log/qqbot-backup.log 2>&1; then
        echo "$(date -Is) offsite rclone copy ok" >>/var/log/qqbot-backup.log
    else
        echo "$(date -Is) offsite rclone copy failed" >&2
    fi
elif [ -n "${BACKUP_RSYNC_TARGET:-}" ] && command -v rsync >/dev/null 2>&1; then
    if timeout 3600 rsync -az "$archive" "$BACKUP_RSYNC_TARGET" >>/var/log/qqbot-backup.log 2>&1; then
        echo "$(date -Is) offsite rsync copy ok" >>/var/log/qqbot-backup.log
    else
        echo "$(date -Is) offsite rsync copy failed" >&2
    fi
fi
