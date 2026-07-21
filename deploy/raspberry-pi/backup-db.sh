#!/usr/bin/env bash
# Backup diario de nodos.db al pendrive montado en BACKUP_MOUNT.
set -euo pipefail

APP_DIR="${APP_DIR:-$HOME/backend_qa_nodos}"
BACKUP_MOUNT="${BACKUP_MOUNT:-/mnt/nodos-backup}"
BACKUP_DIR="${BACKUP_DIR:-$BACKUP_MOUNT/db}"
DB_PATH="${DB_PATH:-$APP_DIR/data/nodos.db}"
RETENTION_DAYS="${RETENTION_DAYS:-30}"
TODAY="$(date +%Y-%m-%d)"
DEST="$BACKUP_DIR/nodos-${TODAY}.db"

log() {
  logger -t nodos-db-backup "$*"
  echo "$*"
}

if [[ ! -d "$BACKUP_MOUNT" ]]; then
  log "ERROR: no existe $BACKUP_MOUNT"
  exit 1
fi

if ! mountpoint -q "$BACKUP_MOUNT"; then
  log "ERROR: $BACKUP_MOUNT no está montado (¿pendrive enchufado?)"
  exit 1
fi

if [[ ! -f "$DB_PATH" ]]; then
  log "ERROR: no se encontró la base en $DB_PATH"
  exit 1
fi

mkdir -p "$BACKUP_DIR"

if command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB_PATH" ".backup '$DEST'"
elif [[ -x "$APP_DIR/.venv/bin/python3" ]]; then
  "$APP_DIR/.venv/bin/python3" - "$DB_PATH" "$DEST" <<'PY'
import sqlite3
import sys

src, dst = sys.argv[1], sys.argv[2]
with sqlite3.connect(f"file:{src}?mode=ro", uri=True) as source:
    with sqlite3.connect(dst) as target:
        source.backup(target)
PY
else
  log "ERROR: falta sqlite3 o el venv de Python"
  exit 1
fi

find "$BACKUP_DIR" -maxdepth 1 -type f -name 'nodos-*.db' -mtime +"$RETENTION_DAYS" -delete 2>/dev/null || true

BYTES="$(wc -c < "$DEST" | tr -d ' ')"
log "OK: $DEST (${BYTES} bytes), retención ${RETENTION_DAYS} días"
