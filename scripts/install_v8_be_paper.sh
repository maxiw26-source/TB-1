#!/usr/bin/env bash
set -euo pipefail
cd /root/TB-1
./venv/bin/python selftest_v8_be_paper.py
./venv/bin/python selftest_dashboard_history.py
cp deploy/lsob-v8-be-paper@.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable lsob-v8-be-paper@BTC lsob-v8-be-paper@ADA
systemctl restart lsob-v8-be-paper@BTC lsob-v8-be-paper@ADA lsob-monitor-dashboard
systemctl is-active lsob-v8-be-paper@BTC lsob-v8-be-paper@ADA lsob-monitor-dashboard
