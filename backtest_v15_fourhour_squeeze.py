"""Research-only 4h volatility squeeze followed by breakout.

Full 4h bars from consecutive 1h bars; closed signals, next 1h open,
stop-first exits and existing V12 fee/slippage/adverse funding accounting.
No exchange orders or configuration changes to running bots.
"""
import argparse
from pathlib import Path

from backtest_v8_walkforward import load_csv
from backtest_v9 import print_result, timestamp_text
from backtest_v12_hourly_trend import (DAY_MS, HOUR_MS, aggregate_hourly,
                                       run_window, write_csv)
from strategy_v9 import indicators

FOUR_MS = 4 * HOUR_MS
BLOCK = 6              # six 4h candles = 24h
HISTORY_BLOCKS = 3     # 72h historical comparison
COMPRESS_RATIO = 0.70
MIN_VOLUME_RATIO = 1.20


def fourhour(hour):
    """Make only fully aligned, gap-free four-hour candles."""
    result = []
    key = None
    group = []
    for c in hour:
        ts = int(c["timestamp"])
        bucket = ts - ts % FOUR_MS
        if bucket != key:
            key, group = bucket, []
        group.append(c)
        if len(group) == 4 and [b["timestamp"] for b in group] == [
                bucket + k * HOUR_MS for k in range(4)]:
            result.append({"timestamp": bucket, "open": group[0]["open"],
                           "high": max(b["high"] for b in group),
                           "low": min(b["low"] for b in group),
                           "close": group[-1]["close"],
                           "volume": sum(b["volume"] for b in group)})
    return result


def block_range(bars):
    return max(b["high"] for b in bars) - min(b["low"] for b in bars)


def signals(hour):
    """A breakout after a compressed PRIOR day, computed at 4h close."""
    out = [None] * len(hour)
    lookup = {b["timestamp"]: i for i, b in enumerate(hour)}
    f = indicators(hour)
    four = fourhour(hour)
    span = BLOCK * (HISTORY_BLOCKS + 1)
    for i in range(span, len(four)):
        # Current candle and all prior comparison blocks must be contiguous.
        subset = four[i - span:i + 1]
        if any(b["timestamp"] - a["timestamp"] != FOUR_MS
               for a, b in zip(subset, subset[1:])):
            continue
        base = four[i - BLOCK:i]
        recent_range = block_range(base)
        older = [block_range(four[i - BLOCK * (k + 2):i - BLOCK * (k + 1)])
                 for k in range(HISTORY_BLOCKS)]
        older.sort()
        if older[1] <= 0 or recent_range <= 0 or recent_range > COMPRESS_RATIO * older[1]:
            continue
        last = four[i]
        avg_volume = sum(b["volume"] for b in base) / BLOCK
        if avg_volume <= 0 or last["volume"] < avg_volume * MIN_VOLUME_RATIO:
            continue
        hour_index = lookup[last["timestamp"] + 3 * HOUR_MS]
        features = f[hour_index]
        if features is None or features["atr"] <= 0:
            continue
        atr = features["atr"]
        top, bottom = max(b["high"] for b in base), min(b["low"] for b in base)
        close = last["close"]
        if top < close <= top + 2 * atr:
            out[hour_index] = ("LONG", atr)
        elif bottom > close >= bottom - 2 * atr:
            out[hour_index] = ("SHORT", atr)
    return out


def main():
    ap = argparse.ArgumentParser(description="V15 historical 4h squeeze, no orders")
    ap.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "ADAUSDT"])
    ap.add_argument("--train-days", type=int, default=180)
    ap.add_argument("--test-days", type=int, default=90)
    args = ap.parse_args()
    if args.train_days < 30 or args.test_days < 30:
        ap.error("Each window must be at least 30 days")
    print("V15 4H SQUEEZE – NUR HISTORISCH, KEINE ORDERS", flush=True)
    print("24h Range <=70% historischer Median, 4h Volumen >=1.2x; "
          "naechste Stunde Einstieg, 2 ATR Stop, 3R Ziel, 24h max.", flush=True)
    print("Taker/Slippage beidseitig, adverse Funding 0.01% je 8h; "
          "TEST ist schon durch fruehere Strategien eingesehen.", flush=True)
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
        hour = aggregate_hourly(five)
        end = int(five[-1]["timestamp"] // HOUR_MS * HOUR_MS) - HOUR_MS
        split = end - args.test_days * DAY_MS
        start = split - args.train_days * DAY_MS
        if not hour or hour[0]["timestamp"] > start - 200 * HOUR_MS:
            print("Zu wenig Historie fuer Warmup.", flush=True)
            continue
        print("5m:", timestamp_text(five[0]["timestamp"]), "bis",
              timestamp_text(five[-1]["timestamp"]), flush=True)
        setups = signals(hour)
        for label, lo, hi, days in (("TRAIN", start, split - HOUR_MS, args.train_days),
                                    ("TEST", split, end, args.test_days)):
            available = sum(lo <= c["timestamp"] <= hi for c in hour)
            print(f"{label} Stunden: {available}/{days * 24}", flush=True)
            if available < days * 24 * .90:
                print(f"{label} Daten unvollstaendig; kein Ergebnis.", flush=True)
                continue
            r = run_window(hour, setups, lo, hi)
            print_result(label, r)
            print(f"{label} Funding-Annahme: {r['funding']:.2f} USDT", flush=True)
            write_csv(Path("v15_results") / f"{symbol}_{label.lower()}.csv", r)
    print("FERTIG – CSVs in v15_results; keine Live-Aenderung.", flush=True)


if __name__ == "__main__":
    main()
