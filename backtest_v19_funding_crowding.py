"""Read-only BTC funding crowding hypothesis; never places exchange orders.

Use settled funding history only. A positive funding extreme after a BTC rise
suggests a short reversal; a negative extreme after a fall suggests a long.
All rules are fixed before running the research. Prior TEST periods have been
examined in earlier experiments, so a positive TEST is not independent proof.
"""
import argparse
import csv
import json
import time
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path

from backtest_v8_walkforward import load_csv
from backtest_v9 import SLIPPAGE_PCT, TAKER_FEE_PCT, timestamp_text
from backtest_v12_hourly_trend import DAY_MS, HOUR_MS, aggregate_hourly

API = "https://fapi.bitunix.com/api/v1/futures/market/get_funding_rate_history"
SYMBOL = "BTCUSDT"
NOTIONAL = 100.0
FEE = TAKER_FEE_PCT / 100
SLIP = SLIPPAGE_PCT / 100
FUNDING_THRESHOLD = .0001  # settled funding >= 0.01% per event
MIN_MOVE = .01  # prior 24h move must align with crowded side
STOP = .02
TARGET = .03
HOLD_HOURS = 24
WINDOW_MS = 7 * DAY_MS


def get_rates(start, end):
    """Fetch in bounded windows; reject partial pagination and API errors."""
    rates = {}
    requests = 0
    for lo in range(start, end + 1, WINDOW_MS):
        hi = min(end, lo + WINDOW_MS - 1)
        params = {"symbol": SYMBOL, "starTime": lo, "endTime": hi, "limit": 200}
        request = urllib.request.Request(API + "?" + urllib.parse.urlencode(params),
                                         headers={"Accept": "application/json", "User-Agent": "TB-1-research/1"})
        try:
            with urllib.request.urlopen(request, timeout=25) as response:
                body = json.load(response)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Funding-API nicht erreichbar: {exc}") from exc
        if not isinstance(body, dict) or body.get("code") != 0 or not isinstance(body.get("data"), list):
            raise RuntimeError(f"Funding-API Fehler im Fenster {timestamp_text(lo)}: {str(body)[:200]}")
        items = body["data"]
        if len(items) >= 200:
            raise RuntimeError("200 Funding-Eintraege im 7-Tage-Fenster: Pagination unklar; kein Ergebnis")
        for item in items:
            ts = int(item["fundingTime"])
            rate = float(item["fundingRate"])
            if lo <= ts <= hi and abs(rate) < .1:
                if ts in rates and rates[ts] != rate:
                    raise RuntimeError("Widerspruechliche Funding-Werte")
                rates[ts] = rate
        requests += 1
        time.sleep(.12)
    return dict(sorted(rates.items())), requests


def evaluate(bars, rates, start, end):
    """Each event uses only settled rates and completed hourly price bars."""
    trades = []
    reasons = Counter()
    rate_times = list(rates)
    last_exit = start
    for event in rate_times:
        entry_ts = (event // HOUR_MS + 1) * HOUR_MS
        if entry_ts < start or entry_ts >= end or entry_ts < last_exit:
            continue
        past = [rates[t] for t in rate_times if event - DAY_MS < t <= event]
        if len(past) < 2:
            reasons["INSUFFICIENT_FUNDING_EVENTS"] += 1
            continue
        prev = bars.get(entry_ts - HOUR_MS)
        prior = bars.get(entry_ts - 25 * HOUR_MS)
        entry_bar = bars.get(entry_ts)
        if not prev or not prior or not entry_bar:
            reasons["PRICE_HISTORY_MISSING"] += 1
            continue
        move = prev["close"] / prior["close"] - 1
        settled = sum(past[-3:]) / min(len(past), 3)
        if settled >= FUNDING_THRESHOLD and move >= MIN_MOVE:
            direction = -1
        elif settled <= -FUNDING_THRESHOLD and move <= -MIN_MOVE:
            direction = 1
        else:
            reasons["NO_SIGNAL"] += 1
            continue
        # No forward-filling. Incomplete trade path invalidates the whole window.
        fill = entry_bar["open"] * (1 + direction * SLIP)
        quantity = NOTIONAL / fill
        stop = fill * (1 - direction * STOP)
        target = fill * (1 + direction * TARGET)
        result = None
        for idx in range(HOLD_HOURS):
            ts = entry_ts + idx * HOUR_MS
            if ts >= end:
                break
            bar = bars.get(ts)
            if bar is None:
                return {"invalid": f"5m-Datenluecke in offenem Trade bei {timestamp_text(ts)}"}
            if (bar["low"] <= stop if direction == 1 else bar["high"] >= stop):
                raw_exit = (min(bar["open"], stop) if direction == 1
                            else max(bar["open"], stop))
                exit_ts, why = ts + HOUR_MS, "STOP_FIRST"
            elif (bar["high"] >= target if direction == 1 else bar["low"] <= target):
                raw_exit, exit_ts, why = target, ts + HOUR_MS, "TARGET"
            elif idx == HOLD_HOURS - 1 or ts + HOUR_MS >= end:
                raw_exit, exit_ts, why = bar["close"], ts + HOUR_MS, "TIME"
            else:
                continue
            result = raw_exit, exit_ts, why
            break
        if result is None:
            return {"invalid": "Offener Trade ohne Exit"}
        raw_exit, exit_ts, why = result
        exit_price = raw_exit * (1 - direction * SLIP)
        actual_funding = sum(-direction * NOTIONAL * rates[t]
                             for t in rate_times if entry_ts < t < exit_ts)
        fees = NOTIONAL * FEE + exit_price * quantity * FEE
        net = direction * (exit_price - fill) * quantity - fees + actual_funding
        trades.append({"entry_time": timestamp_text(entry_ts), "exit_time": timestamp_text(exit_ts),
                       "direction": "LONG" if direction == 1 else "SHORT", "entry": fill,
                       "exit": exit_price, "funding": actual_funding, "fees": fees,
                       "net": net, "reason": why, "past_funding": settled, "past_24h_move": move})
        last_exit = exit_ts
        reasons[why] += 1
    wins = sum(t["net"] > 0 for t in trades)
    gains = sum(max(t["net"], 0) for t in trades)
    losses = -sum(min(t["net"], 0) for t in trades)
    return {"trades": trades, "wins": wins, "net": sum(t["net"] for t in trades),
            "pf": gains / losses if losses else (float("inf") if gains else 0),
            "fees": sum(t["fees"] for t in trades),
            "funding": sum(t["funding"] for t in trades), "reasons": reasons}


def main():
    parser = argparse.ArgumentParser(description="V19 Funding-Extrem, historisch, keine Orders")
    parser.add_argument("--train-days", type=int, default=180)
    parser.add_argument("--test-days", type=int, default=90)
    args = parser.parse_args()
    if args.train_days < 90 or args.test_days < 60:
        parser.error("TRAIN mindestens 90, TEST mindestens 60 Tage")
    print("V19 BTC FUNDING-EXTREM – NUR HISTORISCH, KEINE ORDERS", flush=True)
    print("Nach abgerechneter positiver Funding-Spitze und +1%/24h SHORT; "
          "bei negativer Spitze und -1%/24h LONG. 24h, Stop 2%, Ziel 3%, "
          "100 USDT ohne Hebel; Marktgebuehr/Slippage beidseitig, echtes Funding.", flush=True)
    try:
        five = load_csv(SYMBOL, "5m")
        if not five:
            raise RuntimeError("BTCUSDT 5m fehlt")
        bars = {b["timestamp"]: b for b in aggregate_hourly(five)}
        if not bars:
            raise RuntimeError("Keine vollstaendigen 1h-Kerzen")
        end = min(max(bars) // DAY_MS * DAY_MS, int(time.time() * 1000) // DAY_MS * DAY_MS)
        split = end - args.test_days * DAY_MS
        start = split - args.train_days * DAY_MS
        rates, count = get_rates(start - DAY_MS, end)
        print(f"Funding-API: {count} Anfragen, {len(rates)} eindeutige Settlements von "
              f"{timestamp_text(min(rates)) if rates else '---'} bis "
              f"{timestamp_text(max(rates)) if rates else '---'}", flush=True)
        for label, lo, hi in (("TRAIN", start, split), ("TEST", split, end)):
            n_days = (hi - lo) // DAY_MS
            price_coverage = sum(lo <= t < hi for t in bars) / (n_days * 24)
            rate_days = len({t // DAY_MS for t in rates if lo <= t < hi})
            print(f"{label}: {timestamp_text(lo)} bis {timestamp_text(hi)} | "
                  f"1h-Abdeckung {price_coverage:.1%}, Tage mit Funding {rate_days}/{n_days}", flush=True)
            if price_coverage < .95 or rate_days < n_days * .9:
                print("KEIN ERGEBNIS: Kurs- oder Funding-Historie unvollstaendig.", flush=True)
                continue
            result = evaluate(bars, rates, lo, hi)
            if "invalid" in result:
                print("KEIN ERGEBNIS:", result["invalid"], flush=True)
                continue
            print(f"{label} Trades {len(result['trades'])} | Treffer {result['wins']} | "
                  f"PF {result['pf']:.2f} | Netto {result['net']:+.2f} USDT | "
                  f"Gebuehren {result['fees']:.2f} | Funding {result['funding']:+.2f} | "
                  f"Signale {dict(result['reasons'])}", flush=True)
            folder = Path("v19_results")
            folder.mkdir(exist_ok=True)
            with (folder / f"funding_{label.lower()}.csv").open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=("entry_time", "exit_time", "direction",
                    "entry", "exit", "funding", "fees", "net", "reason", "past_funding", "past_24h_move"))
                writer.writeheader()
                writer.writerows(result["trades"])
    except (ValueError, RuntimeError, OSError) as exc:
        print("KEIN ERGEBNIS:", exc, flush=True)
    print("FERTIG – TEST-Zeitraum mehrfach zuvor eingesehen; kein unabhaengiger Erfolgsnachweis.", flush=True)


if __name__ == "__main__":
    main()
