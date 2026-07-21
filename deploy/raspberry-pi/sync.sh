#!/usr/bin/env bash
# Sincroniza el backend a la Pi y reinicia el servicio.
# Uso: ./deploy/raspberry-pi/sync.sh [usuario@host]
#
# Credenciales opcionales en .env (raíz del repo):
#   PASSWORD_PI='Ubuntu1234$'
#   PI_TARGET=tili@192.168.68.75
#
# Requiere sshpass si usás contraseña: brew install hudochenkov/sshpass/sshpass
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
# shellcheck disable=SC1091
source "$ROOT/deploy/raspberry-pi/_load_env.sh"
_load_env "$ROOT"

TARGET="${1:-${PI_TARGET:-tili@192.168.68.75}}"
_setup_remote_cmds

echo "==> Sync desde $ROOT hacia $TARGET:~/backend_qa_nodos/"

rsync -av --delete -e "$RSYNC_RSH" \
  "$ROOT/profiles/" "$TARGET:~/backend_qa_nodos/profiles/"

rsync -av -e "$RSYNC_RSH" \
  "$ROOT/app.py" \
  "$ROOT/automation.py" \
  "$ROOT/config.py" \
  "$ROOT/readings_hub.py" \
  "$ROOT/storage.py" \
  "$ROOT/sensor_analysis.py" \
  "$TARGET:~/backend_qa_nodos/"

rsync -av -e "$RSYNC_RSH" \
  "$ROOT/templates/" "$TARGET:~/backend_qa_nodos/templates/"

rsync -av -e "$RSYNC_RSH" \
  "$ROOT/static/" "$TARGET:~/backend_qa_nodos/static/"

echo "==> Reiniciando nodos-backend..."
_sudo_remote "systemctl restart nodos-backend"
_remote "sleep 2 && curl -s http://127.0.0.1:3000/api/automation/status | python3 -c \"import sys,json; d=json.load(sys.stdin); p=d.get('cultivation_plan',{}); print(p.get('nombre'), '· día', p.get('dia_cultivo'), '·', p.get('fase_label'))\""

echo "==> Listo."
