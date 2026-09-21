#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/TB-1}"

cd "$REPO_DIR"

cp deploy/lsob-two-hour-status.service /etc/systemd/system/lsob-two-hour-status.service
cp deploy/lsob-two-hour-status.timer /etc/systemd/system/lsob-two-hour-status.timer

systemctl daemon-reload
systemctl enable --now lsob-two-hour-status.timer

echo ""
systemctl status lsob-two-hour-status.timer --no-pager --lines=12

echo ""
systemctl list-timers lsob-two-hour-status.timer --no-pager

echo ""
echo "2-STUNDEN-UPDATE TIMER INSTALLIERT"
