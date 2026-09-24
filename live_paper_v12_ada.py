"""Prospective ADA V12 paper monitor. Public market data only; no orders.

Signals use completed 1h candles; a new position uses the observed price in
the following hour. Closed-hour OHLC resolves stops before targets. This is
an observational simulation, not an execution-quality fill model.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from backtest_v9 import SLIPPAGE_PCT, TAKER_FEE_PCT, close_position
from backtest_v12_hourly_trend import (
    ASSUMED_FUNDING_PCT, HOUR_MS, STOP_ATR, TARGET_R, exit_hit,
    funding_cost, signals,
)
from historical_data import normalize, request_klines

SYMBOL = "ADAUSDT"
NOTIONAL_USDT = 100.0
POLL_SECONDS = 35
HISTORY_HOURS = 300
MAX_ENTRY_DELAY_MS = 120_000
STATE = Path("v12_ada_paper_state.json")
EVENTS = Path("v12_ada_paper_events.jsonl")


def load_env():
    path = Path(".env")
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def default_state():
    return {"last_closed_ts": None, "position": None,
            "stats": {"trades": 0, "wins": 0, "losses": 0,
                      "net_usdt": 0.0, "fees_usdt": 0.0,
                      "funding_assumed_usdt": 0.0}}


def load_state():
    if not STATE.exists():
        return default_state()
    # Fail closed on corrupt state, avoiding duplicated simulated entries.
    payload = json.loads(STATE.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or "stats" not in payload:
        raise ValueError("Ungueltiger V12-Paper-State")
    return payload


def save_state(state):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE)


def event(kind, details):
    with EVENTS.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps({"ts": int(time.time()), "event": kind,
                                 "details": details}, ensure_ascii=False) + "\n")


def notify(message):
    token, chat_id = os.getenv("TELEGRAM_BOT_TOKEN"), os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
    try:
        request = urllib.request.Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=json.dumps({"chat_id": chat_id, "text": message}).encode("utf-8"),
            headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
    except (urllib.error.URLError, OSError) as exc:
        event("NOTIFICATION_ERROR", {"error": str(exc)})


def fetch_closed(now_ms):
    """Get recent completed hourly bars in API pages of up to 200 bars."""
    current_hour = now_ms // HOUR_MS * HOUR_MS
    last = current_hour - HOUR_MS
    first = last - (HISTORY_HOURS - 1) * HOUR_MS
    seen = {}
    cursor = first
    while cursor <= last:
        end = min(cursor + 199 * HOUR_MS, last)
        payload = request_klines(SYMBOL, "1h", cursor - HOUR_MS, end + HOUR_MS)
        if payload.get("code") != 0:
            raise RuntimeError("Bitunix 1h: API-Code " + str(payload.get("code")))
        for raw in payload.get("data") or []:
            c = normalize(raw)
            ts = int(c["timestamp"])
            if cursor <= ts <= end and ts % HOUR_MS == 0:
                seen[ts] = c
        cursor = end + HOUR_MS
    return [seen[t] for t in sorted(seen)]


def fetch_price():
    url = "https://fapi.bitunix.com/api/v1/futures/market/tickers?" + (
        urllib.parse.urlencode({"symbol": SYMBOL}))
    with urllib.request.urlopen(url, timeout=10) as response:
        payload = json.load(response)
    if payload.get("code") != 0:
        raise RuntimeError("Bitunix Ticker: API-Code " + str(payload.get("code")))
    values = payload.get("data") or []
    if isinstance(values, dict):
        values = [values]
    prices = [float(item["lastPrice"]) for item in values if item.get("symbol") == SYMBOL]
    if not prices or prices[0] <= 0:
        raise RuntimeError("ADA-Ticker fehlt oder ist ungueltig")
    return prices[0]


def paper_entry(side, price, atr, timestamp):
    fill = price * (1 + SLIPPAGE_PCT / 100 if side == "LONG" else
                    1 - SLIPPAGE_PCT / 100)
    if fill <= 0 or atr <= 0:
        return None
    qty = NOTIONAL_USDT / fill
    direction = 1 if side == "LONG" else -1
    distance = STOP_ATR * atr
    return {"side": side, "entry": fill, "stop": fill - direction * distance,
            "target": fill + direction * TARGET_R * distance,
            "qty": qty, "entry_ts": timestamp,
            "entry_fee": fill * qty * TAKER_FEE_PCT / 100, "bars": 0,
            "notional_usdt": NOTIONAL_USDT}


def settle(state, raw_exit, reason, timestamp):
    position = state["position"]
    trade = close_position(position, raw_exit, reason, timestamp)
    trade["funding_assumed_usdt"] = funding_cost(position, timestamp)
    trade["net"] -= trade["funding_assumed_usdt"]
    stats = state["stats"]
    stats["trades"] += 1
    stats["wins" if trade["net"] > 0 else "losses"] += 1
    stats["net_usdt"] += trade["net"]
    stats["fees_usdt"] += trade["entry_fee"] + trade["exit_fee"]
    stats["funding_assumed_usdt"] += trade["funding_assumed_usdt"]
    state["position"] = None
    event("EXIT", trade)
    notify("V12 ADA PAPER – Trade geschlossen\n"
           f"Grund: {reason}, Netto: {trade['net']:+.4f} USDT\n"
           f"Paper gesamt: {stats['net_usdt']:+.4f} USDT\nKEINE echte Order")


def process(state, now_ms=None):
    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    current_hour = now_ms // HOUR_MS * HOUR_MS
    expected_last = current_hour - HOUR_MS
    candles = fetch_closed(now_ms)
    if not candles or candles[-1]["timestamp"] != expected_last:
        raise RuntimeError("Aktuell abgeschlossene 1h-Kerze fehlt")
    last_seen = state["last_closed_ts"]
    if last_seen is None:
        state["last_closed_ts"] = expected_last
        save_state(state)
        event("START", {"last_closed_ts": expected_last})
        return "START"
    if expected_last <= last_seen:
        return "WAIT"
    new = [c for c in candles if c["timestamp"] > last_seen]
    sequential = new and new[0]["timestamp"] == last_seen + HOUR_MS
    sequential = sequential and all(b["timestamp"] - a["timestamp"] == HOUR_MS
                                    for a, b in zip(new, new[1:]))
    for c in new:
        position = state["position"]
        if position is None:
            continue
        if c["timestamp"] < position["entry_ts"]:
            continue
        if c["timestamp"] > last_seen + HOUR_MS and not sequential:
            settle(state, float(c["open"]), "GAP", c["timestamp"])
            continue
        position["bars"] += 1
        hit = exit_hit(position, c)
        if hit:
            settle(state, hit[0], hit[1], c["timestamp"])
    # Never simulate an entry for a historical signal after downtime.
    if (state["position"] is None and sequential and
            expected_last - last_seen == HOUR_MS and
            now_ms - current_hour <= MAX_ENTRY_DELAY_MS):
        setup = signals(candles)[-1]
        if setup is not None:
            price = fetch_price()
            if abs(price - float(candles[-1]["close"])) > setup[1]:
                event("SKIP_GAPPED_ENTRY", {"price": price,
                                              "closed_price": candles[-1]["close"]})
                position = None
            else:
                position = paper_entry(setup[0], price, setup[1], current_hour)
            if position is not None:
                state["position"] = position
                event("ENTRY", position)
                notify("V12 ADA PAPER – Entry\n"
                       f"{position['side']} | beobachtet: {price:.5f}\n"
                       f"Stop: {position['stop']:.5f} | Ziel: {position['target']:.5f}\n"
                       f"Paper-Positionswert: {NOTIONAL_USDT:.2f} USDT\nKEINE echte Order")
    state["last_closed_ts"] = expected_last
    save_state(state)
    return "PROCESSED"


def status():
    state = load_state()
    s = state["stats"]
    print("V12 ADA PAPER – KEINE echten Orders")
    print(f"Neue Positionen: {NOTIONAL_USDT:.2f} USDT")
    print("Letzte verarbeitete 1h-Kerze:", state["last_closed_ts"])
    print("Paper-Position offen:", "JA" if state["position"] else "NEIN")
    print(f"Trades: {s['trades']} | Gewinner/Verlierer: {s['wins']}/{s['losses']}")
    print(f"Netto: {s['net_usdt']:+.4f} USDT | Gebuehren: {s['fees_usdt']:.4f} USDT")
    print(f"Funding-Annahme (immer Kosten): {s['funding_assumed_usdt']:.4f} USDT "
          f"({ASSUMED_FUNDING_PCT:.2f}% je 8h)")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "run"
    load_env()
    if cmd == "status":
        status()
    elif cmd == "once":
        state = load_state()
        print(process(state), flush=True)
        status()
    elif cmd == "run":
        print("V12 ADA PAPER gestartet: 100 USDT; KEINE Orders", flush=True)
        state = load_state()
        while True:
            try:
                result = process(state)
                if result != "WAIT":
                    print(result, flush=True)
                time.sleep(POLL_SECONDS)
            except KeyboardInterrupt:
                return
            except Exception as exc:
                event("ERROR", {"error": str(exc)})
                print("V12 PAPER Fehler:", str(exc), flush=True)
                time.sleep(POLL_SECONDS)
    else:
        raise SystemExit("Verfuegbar: run, once, status")


if __name__ == "__main__":
    main()
