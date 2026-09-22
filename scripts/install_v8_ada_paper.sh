#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/TB-1}"
cd "$REPO_DIR"
"$REPO_DIR/venv/bin/python" selftest_v8_ada_paper.py

cp deploy/lsob-v8-ada-paper.service /etc/systemd/system/lsob-v8-ada-paper.service
systemctl daemon-reload
systemctl enable --now lsob-v8-ada-paper
systemctl restart lsob-v8-ada-paper
sleep 2
systemctl status lsob-v8-ada-paper --no-pager --lines=12
echo "ADA V8 PAPER-FORWARD INSTALLIERT; keine echten Orders."
