#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-/root/TB-1}"
SERVICE="${SERVICE:-lsob-v7}"

cd "$REPO_DIR"

if [[ -f venv/bin/activate ]]; then
  source venv/bin/activate
fi

echo "=== SYSTEMD ==="
systemctl status "$SERVICE" --no-pager --lines=10 || true

echo ""
echo "=== BOT STATUS ==="
python live_paper_v7_hardened.py status || true

echo ""
echo "=== LAST 25 LOG LINES ==="
journalctl -u "$SERVICE" -n 25 --no-pager || true

echo ""
echo "=== RECENT LOOP ERRORS ==="
if [[ -f trade_events.csv ]]; then
  grep 'LOOP_ERROR\|LIVE_EXECUTION_BLOCKED' trade_events.csv | tail -n 10 || true
else
  echo "trade_events.csv noch nicht vorhanden"
fi
