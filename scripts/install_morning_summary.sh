#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/TB-1}"

cd "$REPO_DIR"

cp deploy/lsob-morning-summary.service /etc/systemd/system/lsob-morning-summary.service
cp deploy/lsob-morning-summary.timer /etc/systemd/system/lsob-morning-summary.timer

systemctl daemon-reload
systemctl enable --now lsob-morning-summary.timer

echo ""
systemctl status lsob-morning-summary.timer --no-pager --lines=12

echo ""
systemctl list-timers lsob-morning-summary.timer --no-pager

echo ""
echo "MORGENBERICHT-TIMER INSTALLIERT"
