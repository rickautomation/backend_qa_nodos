#!/usr/bin/env bash
# Reinicia nodos-backend si /health falla o hay demasiadas conexiones CLOSE-WAIT.
set -euo pipefail

CLOSE_WAIT_THRESHOLD="${CLOSE_WAIT_THRESHOLD:-400}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:3000/health}"
CLOSE_WAIT="$(ss -Htan state close-wait sport = :3000 2>/dev/null | wc -l)"

log() {
  logger -t nodos-watchdog "$*"
}

if ! curl -sf --max-time 5 "$HEALTH_URL" >/dev/null; then
  log "health check failed (CLOSE-WAIT=${CLOSE_WAIT}), restarting nodos-backend"
  systemctl restart nodos-backend
  exit 0
fi

if [ "$CLOSE_WAIT" -gt "$CLOSE_WAIT_THRESHOLD" ]; then
  log "CLOSE-WAIT=${CLOSE_WAIT} > ${CLOSE_WAIT_THRESHOLD}, restarting nodos-backend"
  systemctl restart nodos-backend
fi
