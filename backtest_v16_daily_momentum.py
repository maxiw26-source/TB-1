"""V16 research-only 30-day long/cash momentum with a 10% trailing stop.

Complete UTC days, closed-day signals, next-day-open execution; fixed
100 USDT unleveraged notional. No order client, no live configuration.
Fee/slippage on both fills and assumed adverse funding at each 8h boundary.
"""
import argparse
import csv
from pathlib import Path

from backtest_v8_walkforward import load_csv
from backtest_v9 import SLIPPAGE_PCT, TAKER_FEE_PCT, timestamp_text
from backtest_v12_hourly_trend import DAY_MS, HOUR_MS, aggregate_hourly

LOOKBACK_DAYS = 30
NOTIONAL_USDT = 100.0
TRAIL_PCT = 10.0
COOLDOWN_DAYS = 7
FUNDING_EVERY_MS = 8 * HOUR_MS
FUNDING_PCT = 0.01  # adverse assumption, not actual historic funding
FEE = TAKER_FEE_PCT / 100.0
SLIP = SLIPPAGE_PCT / 100.0


def daily(hour):
    """Reject days with any missing or misaligned 1h candle."""
    out = []
    bucket = None
    group = []
    for h in hour:
        ts = int(h["timestamp"])
        key = ts - ts % DAY_MS
        if key != bucket:
            bucket, group = key, []
        group.append(h)
        if len(group) == 24 and [b["timestamp"] for b in group] == [
                key + j * HOUR_MS for j in range(24)]:
            out.append({"timestamp": key, "open": group[0]["open"],
                        "high": max(b["high"] for b in group),
                        "low": min(b["low"] for b in group),
                        "close": group[-1]["close"]})
    return out


def signals(days):
    """Day i signal uses its close and 30 PRIOR consecutive day closes."""
    out = [None] * len(days)
    for i in range(LOOKBACK_DAYS, len(days)):
        part = days[i - LOOKBACK_DAYS:i + 1]
        if any(b["timestamp"] - a["timestamp"] != DAY_MS
               for a, b in zip(part, part[1:])):
            continue
        out[i] = days[i]["close"] > days[i - LOOKBACK_DAYS]["close"]
    return out


def funding_cost(entry_ms, exit_ms):
    first = (entry_ms // FUNDING_EVERY_MS + 1) * FUNDING_EVERY_MS
    count = (exit_ms - first) // FUNDING_EVERY_MS + 1 if exit_ms >= first else 0
    return count * NOTIONAL_USDT * FUNDING_PCT / 100


def run_window(days, signal, start, end):
    trades = []
    position = None
    balance = peak = 10000.0
    realized_dd = 0.0
    cooldown_until = 0
    last = None

    def close(raw, reason, ts, exit_ms):
        nonlocal position, balance, peak, realized_dd
        out = raw * (1 - SLIP)
        gross = (out - position["entry"]) * position["qty"]
        exit_fee = out * position["qty"] * FEE
        funding = funding_cost(position["entry_ts"], exit_ms)
        net = gross - position["entry_fee"] - exit_fee - funding
        trades.append({"entry_ts": position["entry_ts"], "exit_ts": ts,
                       "entry": position["entry"], "exit": out,
                       "qty": position["qty"], "gross": gross,
                       "entry_fee": position["entry_fee"],
                       "exit_fee": exit_fee, "funding_cost": funding,
                       "net": net, "reason": reason})
        balance += net
        peak = max(peak, balance)
        realized_dd = max(realized_dd, 100 * (peak - balance) / peak)
        position = None

    for i, day in enumerate(days):
        ts = day["timestamp"]
        if ts < start or ts > end:
            continue
        last = day
        gap = i == 0 or ts - days[i - 1]["timestamp"] != DAY_MS
        if gap:
            if position is not None:
                close(day["open"], "DATA_GAP", ts, ts)
            cooldown_until = max(cooldown_until, ts + COOLDOWN_DAYS * DAY_MS)
        prev = signal[i - 1] if i else None
        if position is not None and prev is not True:
            close(day["open"], "REGIME", ts, ts)
        if position is None and prev is True and not gap and ts >= cooldown_until:
            entry = day["open"] * (1 + SLIP)
            if entry > 0:
                position = {"entry": entry, "qty": NOTIONAL_USDT / entry,
                            "entry_ts": ts, "entry_fee": NOTIONAL_USDT * FEE,
                            "peak": entry}
        if position is not None:
            stop = position["peak"] * (1 - TRAIL_PCT / 100)
            if day["low"] <= stop:
                # No assumption about whether today's high preceded its low.
                close(min(day["open"], stop), "STOP", ts, ts + DAY_MS)
                cooldown_until = ts + COOLDOWN_DAYS * DAY_MS
            else:
                position["peak"] = max(position["peak"], day["high"])
    if position is not None:
        close(last["close"], "WINDOW_END", last["timestamp"], last["timestamp"] + DAY_MS)
    gains = sum(t["net"] for t in trades if t["net"] > 0)
    losses = -sum(t["net"] for t in trades if t["net"] <= 0)
    return {"trades": trades, "count": len(trades),
            "net": balance - 10000.0,
            "pf": gains / losses if losses else (float("inf") if gains else 0.0),
            "win_pct": 100 * sum(t["net"] > 0 for t in trades) / len(trades) if trades else 0,
            "fees": sum(t["entry_fee"] + t["exit_fee"] for t in trades),
            "funding": sum(t["funding_cost"] for t in trades),
            "realized_dd": realized_dd}


def buy_hold_baseline(days, start, end):
    """Same 100 USDT and modeled costs; require entry and exit days."""
    bars = [d for d in days if start <= d["timestamp"] <= end]
    if not bars or bars[0]["timestamp"] != start or bars[-1]["timestamp"] != end:
        return None
    entry = bars[0]["open"] * (1 + SLIP)
    exit_price = bars[-1]["close"] * (1 - SLIP)
    qty = NOTIONAL_USDT / entry
    funding = funding_cost(start, end + DAY_MS)
    return ((exit_price - entry) * qty - NOTIONAL_USDT * FEE
            - exit_price * qty * FEE - funding)


def main():
    ap = argparse.ArgumentParser(description="V16 historical long/cash only")
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "ADAUSDT"])
    ap.add_argument("--train-days", type=int, default=180)
    ap.add_argument("--test-days", type=int, default=90)
    args = ap.parse_args()
    if args.train_days < 30 or args.test_days < 30:
        ap.error("Each window must be >=30 days")
    print("V16 30-TAGE MOMENTUM – NUR HISTORISCH, KEINE ORDERS", flush=True)
    print("Long/Cash; 30 Tage Trend, 10% Trailing-Stop, 7 Tage Cooldown; "
          "100 USDT pro Position, keine Hebelannahme.", flush=True)
    print("Taker/Slippage beidseitig; Funding-Annahme 0.01% je 8h immer Kosten. "
          "TEST-Daten schon bei frueheren Strategien angesehen.", flush=True)
    for symbol in [s.upper() for s in args.symbols]:
        print("\n" + symbol, flush=True)
        try:
            five = load_csv(symbol, "5m")
        except FileNotFoundError as exc:
            print("Fehlende Daten:", exc, flush=True)
            continue
        if len(five) < 3000 or any(b["timestamp"] <= a["timestamp"]
                                   for a, b in zip(five, five[1:])):
            print("Zu wenige oder doppelte Daten.", flush=True)
            continue
        days = daily(aggregate_hourly(five))
        # Exclude last possibly incomplete UTC day; only fully completed days.
        end = (int(five[-1]["timestamp"]) // DAY_MS - 1) * DAY_MS
        split = end - (args.test_days - 1) * DAY_MS
        start = split - args.train_days * DAY_MS
        if not days or days[0]["timestamp"] > start - LOOKBACK_DAYS * DAY_MS:
            print("Zu wenig vollstaendige Tage fuer Warmup.", flush=True)
            continue
        print("5m:", timestamp_text(five[0]["timestamp"]), "bis",
              timestamp_text(five[-1]["timestamp"]), flush=True)
        sig = signals(days)
        for label, lo, hi, n in (("TRAIN", start, split - DAY_MS, args.train_days),
                                 ("TEST", split, end, args.test_days)):
            available = sum(lo <= d["timestamp"] <= hi for d in days)
            print(f"{label} Tage: {available}/{n}", flush=True)
            if available < n * 0.90:
                print(f"{label} Daten unvollstaendig; kein Ergebnis.", flush=True)
                continue
            result = run_window(days, sig, lo, hi)
            print(f"{label} | Trades {result['count']} | Treffer {result['win_pct']:.1f}% | "
                  f"PF {result['pf']:.2f} | Netto {result['net']:+.2f} USDT | "
                  f"Gebuehren {result['fees']:.2f} | "
                  f"Funding-Annahme {result['funding']:.2f} | "
                  f"Realisiert-DD {result['realized_dd']:.2f}%", flush=True)
            hold = buy_hold_baseline(days, lo, hi)
            print(f"{label} 100-USDT-Buy-and-Hold netto: "
                  f"{hold:+.2f} USDT" if hold is not None else
                  f"{label} Buy-and-Hold nicht berechnet wegen fehlendem Randtag.", flush=True)
            out = Path("v16_results") / f"{symbol}_{label.lower()}.csv"
            out.parent.mkdir(exist_ok=True)
            with out.open("w", encoding="utf-8", newline="") as f:
                fields = ("entry_ts", "exit_ts", "entry", "exit", "qty", "gross",
                          "entry_fee", "exit_fee", "funding_cost", "net", "reason")
                writer = csv.DictWriter(f, fieldnames=fields)
                writer.writeheader()
                writer.writerows(result["trades"])
    print("FERTIG – CSVs in v16_results; keine Live-Aenderung.", flush=True)


if __name__ == "__main__":
    main()
