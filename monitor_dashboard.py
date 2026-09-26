"""Read-only local dashboard for the running LSOB services.

Only binds to loopback. No order endpoints, credentials, or raw state payloads.
"""
from dashboard_history import paper_history, live_history
import argparse
import json
import os
import re
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
    ("v20_ada", "V20 · ADA Support / Resistance", "lsob-v20-ada-paper", "paper", "v20_ada_paper_state.json"),
    ("v7", "V7 · BTC / ETH", "lsob-v7", "real", "runtime_state.json"),
    ("v8_btc", "V8 · BTC Balanced", "lsob-v8-paper", "paper", "v8_paper_state.json"),
    ("v8_ada", "V8 · ADA Expiry", "lsob-v8-ada-paper", "paper", "v8_ada_paper_state.json"),
    ("v8_btc_be", "V8 · BTC 2R + Break-even Test", "lsob-v8-be-paper@BTC", "paper", "v8_btc_be_paper_state.json"),
    ("v8_ada_be", "V8 · ADA 2R + Break-even Test", "lsob-v8-be-paper@ADA", "paper", "v8_ada_be_paper_state.json"),
    ("v12_ada", "V12 · ADA 1H Trend", "lsob-v12-ada-paper", "paper", "v12_ada_paper_state.json"),
)
GRID_COINS = ('BTC', 'ETH', 'SOL', 'ADA', 'DOGE', 'SUI')
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


def v7_signal_reasons():
    try:
        result = subprocess.run(
            ["journalctl", "-u", "lsob-v7", "--since=-10min", "-n", "80",
             "--output=cat", "--no-pager"],
            capture_output=True, text=True, timeout=3, check=False,
        )
        reasons = {}
        for line in result.stdout.splitlines():
            match = re.search(r"\b(BTCUSDT|ETHUSDT) Signal: (.*?) \| (.*)$", line)
            if match:
                reasons[match.group(1)] = (match.group(3).strip() or match.group(2).strip())[:100]
        return reasons
    except (OSError, subprocess.TimeoutExpired):
        return {}


def position_view(position):
    if not isinstance(position, dict):
        return None
    if position.get("exit_policy") in ("full_2r_v1", "full_2r_be1r_v1"):
        position = dict(position, target=position.get("tp2"))
        position.pop("tp1", None)
    return {key: (str(position[key]) if key == "side" else number(position[key]))
            for key in ("side", "entry", "stop", "target", "tp1", "tp2", "qty")
            if position.get(key) is not None}


def paper_view(payload, version):
    stats = payload.get("stats") or {}
    position = payload.get("position")
    pending = payload.get("pending") if version not in ("v12_ada", "v20_ada") else None
    stamp = payload.get("last_closed_ts" if version in ("v12_ada", "v20_ada")
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
        "pnl": number(stats.get("net_usdt") if version in ("v12_ada", "v20_ada") else stats.get("net_pnl_usdt")),
        "candle_at": candle_at,
    }
    if version in ("v12_ada", "v20_ada"):
        report["fees"] = number(stats.get("fees_usdt"))
        report["funding_assumed"] = number(stats.get("funding_assumed_usdt"))
    else:
        phase = (payload.get("stats_by_notional") or {}).get("100.00") or {}
        report["phase_100_pnl"] = number(phase.get("net_pnl_usdt"))
        report["phase_100_trades"] = int(phase.get("trades") or 0)
    if version in ("v8_btc", "v8_ada"):
        report["phase_2r"] = payload.get("stats_by_exit_policy", {}).get("full_2r_v1", {})
        report["exit_rule"] = "Neue Trades: 1:2 vor Kosten · kein Teilverkauf"
        report["legacy_position"] = bool(position and position.get("exit_policy") != "full_2r_v1")
    if version in ("v8_btc_be", "v8_ada_be"):
        report["experiment"] = True
        report["phase_2r"] = payload.get("stats_by_exit_policy", {}).get("full_2r_be1r_v1", {})
        report["exit_rule"] = "2R · BE ab +1R am Kerzenschluss, sobald Kosten gedeckt · Puffer 0,01 USDT · Funding-Annahme 0"
        report["be_status"] = "Kostendeckender Stop aktiv" if position and position.get("be_armed") else "BE noch nicht aktiv"
    if version == "v20_ada":
        report["stage"] = payload.get("stage") or report["stage"]
        report["error"] = payload.get("error")
        report["notional"] = number(payload.get("notional_usdt"))
        report["unrealized_gross"] = number(payload.get("unrealized_gross"))
        report["halted"] = bool(payload.get("halted"))
        stamp = payload.get("last_closed_ts")
        if not stamp or time.time() - (int(stamp) + 300000) / 1000 > 600:
            report["error"] = report["error"] or "Keine aktuellen 5m-Marktdaten"
            report["stage"] = "Marktdaten veraltet"
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
                 "history": live_history(ROOT, history),
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
    v7_reasons = v7_signal_reasons()
    for key, label, service, mode, filename in SERVICES:
        payload, mtime, error = read_state(filename)
        service_state = states.get(service, "unbekannt")
        # V12 writes once per completed hour; allow 15 minutes after the
        # following close before reporting an actual missed update.
        max_age = 75 * 60 if key == "v12_ada" else 180
        heartbeat = max((ts for ts in (mtime, v7_log if key == "v7" else None)
                         if ts is not None), default=None)
        age = round(now - heartbeat) if heartbeat else None
        fresh = age is not None and 0 <= age <= max_age
        data = (v7_view(payload) if key == "v7" else paper_view(payload, key)) if payload else {}
        if key != "v7":
            data["history"] = paper_history(ROOT, key)
        if key == "v7":
            data["signal_reasons"] = v7_reasons
        if service_state != "active":
            data["stage"] = "Dienst aus" if service_state == "inactive" else "Dienst prüfen"
        elif not fresh:
            data["stage"] = "Daten veraltet" if mtime else "Keine Statusdaten"
        bots.append({"id": key, "label": label, "mode": mode,
                     "service": service_state, "fresh": fresh, "state_at": iso(mtime) if mtime else None,
                     "age_seconds": age, "error": error, **data})
    bots.extend(grid_paper_view(now, coin) for coin in GRID_COINS)
    return {"generated_at": iso(now), "bots": bots, "v7_exchange": live_view()}


def grid_paper_view(now, coin):
    """Read-only, allowlisted public-facing summary; never expose raw state."""
    assert coin in GRID_COINS
    status_path = Path(f"/home/botlab/backtest-lab/results/{coin.lower()}_grid_forward_7d/status.json")
    info = {"id": f"{coin.lower()}_grid_paper", "kind": "grid",
            "coin": coin, "label": f"{coin} · Spot Grid · 7 Tage",
            "mode": "paper", "service": "inactive", "fresh": False,
            "stage": "Keine Statusdaten", "state_at": None,
            "age_seconds": None, "error": None}
    try:
        mtime = status_path.stat().st_mtime
        payload = json.loads(status_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or payload.get("mode") != "PAPER_NO_ORDERS":
            raise ValueError("invalid paper status")
    except (OSError, ValueError):
        info["error"] = "Grid-Paper-Status nicht verfügbar"
        return info
    age = round(now - mtime)
    fresh = 0 <= age <= 180
    finished = payload.get("state") == "FINISHED"
    running = payload.get("state") == "RUNNING"
    info.update(service="finished" if finished else "active" if running and fresh else "inactive",
                fresh=fresh, state_at=iso(mtime), age_seconds=age,
                stage="Test abgeschlossen" if finished else
                      "Daten veraltet" if not fresh else
                      "Handelspause · Ausbruchsschutz" if payload.get("paused") else
                      "Grid aktiv · wartet auf Kursbewegung",
                pnl=number(payload.get("liquidation_pnl_usdt")),
                equity=number(payload.get("liquidation_equity_usdt")),
                quote=number(payload.get("quote_usdt")),
                base=number(payload.get("base_ada")),
                bid=number(payload.get("bid")),
                lower=number(payload.get("lower")),
                upper=number(payload.get("upper")),
                max_drawdown_pct=number(payload.get("max_drawdown_pct")),
                fills=int(payload.get("simulated_fills") or 0),
                buys=int(payload.get("buys") or 0),
                sells=int(payload.get("sells") or 0),
                pauses=int(payload.get("pause_count") or 0),
                paused=bool(payload.get("paused")),
                fees=number(payload.get("fees_usdt")),
                slippage=number(payload.get("slippage_usdt")),
                started_at=payload.get("started_utc"),
                ends_at=payload.get("ends_utc"))
    return info


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