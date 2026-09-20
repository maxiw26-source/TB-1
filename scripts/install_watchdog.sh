#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/TB-1}"

cd "$REPO_DIR"

echo "[1/5] Watchdog-Dateien installieren"
cp deploy/lsob-watchdog.service /etc/systemd/system/lsob-watchdog.service
cp deploy/lsob-watchdog.timer /etc/systemd/system/lsob-watchdog.timer

echo "[2/5] systemd neu laden"
systemctl daemon-reload

echo "[3/5] Timer aktivieren"
systemctl enable --now lsob-watchdog.timer

echo "[4/5] Ersten Check starten"
systemctl start lsob-watchdog.service

echo "[5/5] Status"
systemctl status lsob-watchdog.timer --no-pager --lines=8
echo ""
systemctl status lsob-watchdog.service --no-pager --lines=8 || true
echo ""
echo "WATCHDOG INSTALLIERT"
