#!/usr/bin/env bash
# =============================================================================
# backup.sh — Database backup script for Restaurant SaaS
#
# SQLite: copies the .db file with a timestamp
# PostgreSQL: uses pg_dump
#
# Usage:
#   bash scripts/backup.sh
#   DB_PATH=/data/restaurant.db BACKUP_DIR=/data/backups bash scripts/backup.sh
#
# Cron example (daily at 3 AM):
#   0 3 * * * /app/scripts/backup.sh >> /var/log/backup.log 2>&1
# =============================================================================

set -euo pipefail

BACKUP_DIR="${BACKUP_DIR:-./backups}"
DB_PATH="${DB_PATH:-./restaurant.db}"
DATABASE_URL="${DATABASE_URL:-}"
KEEP_DAYS="${KEEP_DAYS:-7}"
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")

mkdir -p "$BACKUP_DIR"

if [ -n "$DATABASE_URL" ]; then
  # ── PostgreSQL backup ──────────────────────────────────────────────────────
  BACKUP_FILE="$BACKUP_DIR/restaurant_pg_${TIMESTAMP}.sql.gz"
  echo "[$(date)] Starting PostgreSQL backup → $BACKUP_FILE"
  pg_dump "$DATABASE_URL" | gzip > "$BACKUP_FILE"
  echo "[$(date)] PostgreSQL backup complete ($(du -sh "$BACKUP_FILE" | cut -f1))"
else
  # ── SQLite backup ──────────────────────────────────────────────────────────
  if [ ! -f "$DB_PATH" ]; then
    echo "[$(date)] ERROR: DB_PATH=$DB_PATH not found"
    exit 1
  fi
  BACKUP_FILE="$BACKUP_DIR/restaurant_${TIMESTAMP}.db"
  echo "[$(date)] Starting SQLite backup → $BACKUP_FILE"
  # Use SQLite online backup if sqlite3 CLI available, else plain copy
  if command -v sqlite3 &>/dev/null; then
    sqlite3 "$DB_PATH" ".backup '$BACKUP_FILE'"
  else
    cp "$DB_PATH" "$BACKUP_FILE"
  fi
  echo "[$(date)] SQLite backup complete ($(du -sh "$BACKUP_FILE" | cut -f1))"
fi

# ── Cleanup old backups ────────────────────────────────────────────────────
if command -v find &>/dev/null; then
  DELETED=$(find "$BACKUP_DIR" -name "restaurant_*" -mtime +"$KEEP_DAYS" -print -delete | wc -l)
  [ "$DELETED" -gt 0 ] && echo "[$(date)] Deleted $DELETED backups older than ${KEEP_DAYS} days"
fi

echo "[$(date)] Backup done: $BACKUP_FILE"
