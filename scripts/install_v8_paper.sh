#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/TB-1}"

cd "$REPO_DIR"

cp deploy/lsob-v8-paper.service /etc/systemd/system/lsob-v8-paper.service

systemctl daemon-reload
systemctl enable --now lsob-v8-paper
systemctl restart lsob-v8-paper

sleep 2

systemctl status lsob-v8-paper --no-pager --lines=12

echo ""
echo "V8 PAPER MONITOR INSTALLIERT"
