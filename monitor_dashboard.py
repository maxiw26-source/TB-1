"""Read-only local dashboard for the running LSOB services.

Only binds to loopback. No order endpoints, credentials, or raw state payloads.
"""
import argparse
import json
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "monitor_dashboard.html"
SERVICES = (
    ("v7", "V7 · BTC / ETH", "lsob-v7", "real", "runtime_state.json"),
    ("v8_btc", "V8 · BTC Balanced", "lsob-v8-paper", "paper", "v8_paper_state.json"),
    ("v8_ada", "V8 · ADA Expiry", "lsob-v8-ada-paper", "paper", "v8_ada_paper_state.json"),
    ("v12_ada", "V12 · ADA 1H Trend", "lsob-v12-ada-paper", "paper", "v12_ada_paper_state.json"),
)
LIVE_CACHE = {"at": 0, "loading": False, "value": None}
LIVE_LOCK = threading.Lock()


def iso(ts):
    return datetime.fromtimestamp(ts, timezone.utc).isoformat()


def number(value):
    try:
        return round(float(value), 4)
    except (ValueError, TypeError, OverflowError):
        return None


def read_state(name):
    path = ROOT / name
    if not path.exists():
        return None, None, "Statusdatei fehlt"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("kein JSON-Objekt")
        return payload, path.stat().st_mtime, None
    except (OSError, ValueError) as exc:
        return None, None, f"Statusdatei nicht lesbar: {type(exc).__name__}"


def systemd_states():
    names = [service for _, _, service, _, _ in SERVICES]
    mapping = {}
    for service in names:
        try:
            result = subprocess.run(
                ["systemctl", "show", service, "--property=ActiveState", "--value"],
                capture_output=True, text=True, timeout=2, check=False,
            )
            mapping[service] = result.stdout.strip() or "unbekannt"
        except (OSError, subprocess.TimeoutExpired):
            mapping[service] = "unbekannt"
    return mapping


def v7_last_log():
    try:
        result = subprocess.run(
            ["journalctl", "-u", "lsob-v7", "-n", "1", "--output=json", "--no-pager"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        row = json.loads(result.stdout.splitlines()[-1])
        return int(row["__REALTIME_TIMESTAMP"]) / 1_000_000
    except (OSError, ValueError, IndexError, KeyError, subprocess.TimeoutExpired):
        return None


def position_view(position):
    if not isinstance(position, dict):
        return None
    return {key: (str(position[key]) if key == "side" else number(position[key]))
            for key in ("side", "entry", "stop", "target", "tp1", "tp2", "qty")
            if position.get(key) is not None}


def paper_view(payload, version):
    stats = payload.get("stats") or {}
    position = payload.get("position")
    pending = payload.get("pending") if version != "v12_ada" else None
    stamp = payload.get("last_closed_ts" if version == "v12_ada"
                        else "last_closed_candle")
    try:
        candle_at = iso(int(stamp) / 1000) if stamp else None
    except (ValueError, TypeError, OverflowError):
        candle_at = None
    report = {
        "stage": "Position offen" if position else "Signal bereit" if pending else "Wartet auf Signal",
        "position": position_view(position),
        "pending": position_view(pending.get("setup")) if isinstance(pending, dict) else None,
        "trades": int(stats.get("trades") or 0),
        "wins": int(stats.get("wins") or 0),
        "losses": int(stats.get("losses") or 0),
        "pnl": number(stats.get("net_usdt") if version == "v12_ada" else stats.get("net_pnl_usdt")),
        "candle_at": candle_at,
    }
    if version == "v12_ada":
        report["fees"] = number(stats.get("fees_usdt"))
        report["funding_assumed"] = number(stats.get("funding_assumed_usdt"))
    else:
        phase = (payload.get("stats_by_notional") or {}).get("100.00") or {}
        report["phase_100_pnl"] = number(phase.get("net_pnl_usdt"))
        report["phase_100_trades"] = int(phase.get("trades") or 0)
    return report


def v7_view(payload):
    positions = payload.get("open_positions") or {}
    setups = payload.get("pending_setups") or {}
    approval, _, _ = read_state("pending_approval.json")
    waiting = isinstance(approval, dict) and approval.get("status") == "WAITING"
    return {
        "stage": "Bestätigung offen" if waiting else "Position offen" if positions
                 else "Signal bereit" if setups else "Wartet auf Signal",
        "position_count": len(positions) if isinstance(positions, dict) else 0,
        "pending_count": len(setups) if isinstance(setups, dict) else 0,
        "approval_waiting": waiting,
        "pnl": None,
    }


def fetch_live():
    """Existing Bitunix read-only requests; never treat an API error as zero."""
    try:
        os.chdir(ROOT)
        from morning_summary import SYMBOLS, get_history_positions, load_env_file, dec
        from bitunix_live import get_pending_positions
        load_env_file()
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=30)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        # The history endpoint is capped at 100 per symbol: show the scope in UI.
        history = [item for symbol in SYMBOLS for item in get_history_positions(symbol)]
        def total(start):
            return sum((dec(row.get("realizedPNL")) - dec(row.get("fee"))
                        + dec(row.get("funding"))
                        for row in history if int(row.get("mtime") or 0) >= int(start.timestamp() * 1000)),
                       Decimal("0"))
        open_positions = get_pending_positions()
        value = {"last_30d": number(total(since)), "today_utc": number(total(today)),
                 "open_count": sum(1 for p in open_positions if dec(p.get("qty")) > 0),
                 "unrealized": number(sum((dec(p.get("unrealizedPNL")) for p in open_positions
                                           if dec(p.get("qty")) > 0), Decimal("0"))),
                 "error": None, "updated_at": iso(time.time())}
    except Exception as exc:
        value = {"error": f"Bitunix-Abfrage fehlgeschlagen: {type(exc).__name__}",
                 "updated_at": iso(time.time())}
    with LIVE_LOCK:
        LIVE_CACHE.update({"at": time.monotonic(), "loading": False, "value": value})


def live_view():
    with LIVE_LOCK:
        if not LIVE_CACHE["loading"] and time.monotonic() - LIVE_CACHE["at"] >= 90:
            LIVE_CACHE["loading"] = True
            threading.Thread(target=fetch_live, daemon=True).start()
        return dict(LIVE_CACHE["value"] or {"error": "Bitunix-Daten werden geladen"})


def collect():
    states = systemd_states()
    bots = []
    now = time.time()
    v7_log = v7_last_log()
    for key, label, service, mode, filename in SERVICES:
        payload, mtime, error = read_state(filename)
        service_state = states.get(service, "unbekannt")
        max_age = 900 if key == "v12_ada" else 180
        heartbeat = max((ts for ts in (mtime, v7_log if key == "v7" else None)
                         if ts is not None), default=None)
        age = round(now - heartbeat) if heartbeat else None
        fresh = age is not None and 0 <= age <= max_age
        data = (v7_view(payload) if key == "v7" else paper_view(payload, key)) if payload else {}
        if service_state != "active":
            data["stage"] = "Dienst aus" if service_state == "inactive" else "Dienst prüfen"
        elif not fresh:
            data["stage"] = "Daten veraltet" if mtime else "Keine Statusdaten"
        bots.append({"id": key, "label": label, "mode": mode,
                     "service": service_state, "fresh": fresh, "state_at": iso(mtime) if mtime else None,
                     "age_seconds": age, "error": error, **data})
    return {"generated_at": iso(now), "bots": bots, "v7_exchange": live_view()}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        if path == "/api/status":
            content = json.dumps(collect(), ensure_ascii=False, allow_nan=False).encode()
            mime = "application/json; charset=utf-8"
        elif path in ("/", "/index.html"):
            content = PAGE.read_bytes()
            mime = "text/html; charset=utf-8"
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format, *args):
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    print(f"LSOB Monitor (read-only): http://127.0.0.1:{args.port}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
