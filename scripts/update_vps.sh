#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/TB-1}"
SERVICE="${SERVICE:-lsob-v7}"

cd "$REPO_DIR"

echo "[1/6] Git update"
git fetch origin
git pull --ff-only

echo "[2/6] Activate venv"
source venv/bin/activate

echo "[3/6] Syntax check"
python -m py_compile   config.py   strategy_v7.py   bot_runtime.py   telegram_approval.py   bitunix_live.py   live_paper_v7_hardened.py   live_preflight.py   selftest_v7.py   selftest_live_guardrails.py

echo "[4/6] Selftests"
python selftest_v7.py
python selftest_live_guardrails.py

echo "[5/6] Restart service"
systemctl restart "$SERVICE"

echo "[6/6] Verify service"
sleep 2
systemctl is-active --quiet "$SERVICE"
systemctl status "$SERVICE" --no-pager --lines=8

echo ""
echo "UPDATE ERFOLGREICH"
