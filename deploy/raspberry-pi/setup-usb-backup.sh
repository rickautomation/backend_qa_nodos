#!/usr/bin/env bash
# Formatea el pendrive (si hace falta), monta persistente e instala backup diario.
# Uso en la Pi: ~/backend_qa_nodos/deploy/raspberry-pi/setup-usb-backup.sh [dispositivo]
# Ejemplo:     ~/backend_qa_nodos/deploy/raspberry-pi/setup-usb-backup.sh /dev/sda
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
USB_DISK="${1:-/dev/sda}"
USB_PART="${USB_DISK}1"
MOUNT_POINT="/mnt/nodos-backup"
LABEL="NODOSBK"

if [[ ! -d "$APP_DIR/deploy/raspberry-pi" ]]; then
  echo "ERROR: no encuentro $APP_DIR/deploy/raspberry-pi"
  exit 1
fi

if [[ ! -b "$USB_DISK" ]]; then
  echo "ERROR: no existe el disco $USB_DISK"
  echo "Dispositivos USB:"
  lsblk -o NAME,SIZE,TYPE,TRAN,MOUNTPOINTS | grep -E 'usb|NAME' || lsblk
  exit 1
fi

if [[ "$USB_DISK" == /dev/mmcblk* ]]; then
  echo "ERROR: $USB_DISK parece la SD del sistema, abortando"
  exit 1
fi

echo "==> Pendrive: $USB_DISK → $MOUNT_POINT (label $LABEL)"

if [[ ! -b "$USB_PART" ]]; then
  echo "==> Creando partición FAT32..."
  sudo parted "$USB_DISK" --script mklabel msdos mkpart primary fat32 1MiB 100%
  sleep 2
  sudo mkfs.vfat -F 32 -n "$LABEL" "$USB_PART"
fi

UUID="$(sudo blkid -s UUID -o value "$USB_PART")"
echo "==> UUID: $UUID"

sudo mkdir -p "$MOUNT_POINT"
FSTAB_LINE="UUID=$UUID $MOUNT_POINT vfat defaults,nofail,x-systemd.device-timeout=10,uid=$(id -u),gid=$(id -g),umask=0022 0 2"
if ! grep -q "$MOUNT_POINT" /etc/fstab 2>/dev/null; then
  echo "$FSTAB_LINE" | sudo tee -a /etc/fstab >/dev/null
else
  echo "==> Entrada fstab ya existe para $MOUNT_POINT"
fi

sudo mount "$MOUNT_POINT" || sudo mount -a
mkdir -p "$MOUNT_POINT/db"

chmod +x "$APP_DIR/deploy/raspberry-pi/backup-db.sh"

sudo sed "s|__APP_DIR__|$APP_DIR|g; s|__USER__|$(whoami)|g" \
  "$APP_DIR/deploy/raspberry-pi/nodos-db-backup.service" \
  | sudo tee /etc/systemd/system/nodos-db-backup.service >/dev/null
sudo cp "$APP_DIR/deploy/raspberry-pi/nodos-db-backup.timer" \
  /etc/systemd/system/nodos-db-backup.timer

sudo systemctl daemon-reload
sudo systemctl enable nodos-db-backup.timer
sudo systemctl start nodos-db-backup.timer

echo "==> Probando backup ahora..."
sudo systemctl start nodos-db-backup.service
sleep 1
sudo systemctl status nodos-db-backup.service --no-pager -l | head -15
echo ""
ls -lh "$MOUNT_POINT/db/" | tail -5
echo ""
echo "Listo. Backups diarios a las 03:30 en $MOUNT_POINT/db/"
