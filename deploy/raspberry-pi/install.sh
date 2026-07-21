#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${1:-$HOME/backend_qa_nodos}"
SERVICE_NAME="nodos-backend"

echo "==> Instalando backend en: $APP_DIR"

sudo apt-get update
sudo apt-get install -y python3 python3-venv python3-pip

cd "$APP_DIR"
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

LOCAL_IP="$(hostname -I | awk '{print $1}')"
PI_USER="$(whoami)"
echo ""
echo "IP detectada en la Pi: $LOCAL_IP"
echo "Usuario del servicio: $PI_USER"
echo "Actualiza Firebase remote_config.backend_host = $LOCAL_IP"
echo "Actualiza Firebase remote_config.backend_port = 3000"
echo ""

sed "s|__APP_DIR__|$APP_DIR|g; s|__USER__|$PI_USER|g" \
    "$APP_DIR/deploy/raspberry-pi/nodos-backend.service" \
    | sudo tee "/etc/systemd/system/${SERVICE_NAME}.service" > /dev/null

sudo cp "$APP_DIR/deploy/raspberry-pi/nodos-backend-restart.service" \
    "/etc/systemd/system/nodos-backend-restart.service"
sudo cp "$APP_DIR/deploy/raspberry-pi/nodos-backend-restart.timer" \
    "/etc/systemd/system/nodos-backend-restart.timer"
chmod +x "$APP_DIR/deploy/raspberry-pi/health-watchdog.sh"
sed "s|__APP_DIR__|$APP_DIR|g" \
    "$APP_DIR/deploy/raspberry-pi/nodos-backend-watchdog.service" \
    | sudo tee "/etc/systemd/system/nodos-backend-watchdog.service" > /dev/null
sudo cp "$APP_DIR/deploy/raspberry-pi/nodos-backend-watchdog.timer" \
    "/etc/systemd/system/nodos-backend-watchdog.timer"

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl enable nodos-backend-restart.timer
sudo systemctl enable nodos-backend-watchdog.timer
sudo systemctl restart "$SERVICE_NAME"
sudo systemctl start nodos-backend-restart.timer
sudo systemctl start nodos-backend-watchdog.timer

echo ""
echo "Servicio activo. Verifica con:"
echo "  curl http://127.0.0.1:3000/health"
echo "  sudo systemctl status $SERVICE_NAME"
echo "  sudo journalctl -u $SERVICE_NAME -f"
