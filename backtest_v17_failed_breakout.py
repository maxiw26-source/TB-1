"""V17 research only: failed prior-day channel breakout reversal.

Complete 1h bars from 5m; signal only after closed hour; next-hour open.
V13's independent stop-first cost/exit engine applies on every trade.
No exchange/order API or change to existing live/paper bots.
"""
import argparse
from pathlib import Path

from backtest_v8_walkforward import load_csv
from backtest_v9 import timestamp_text
from backtest_v12_hourly_trend import DAY_MS, HOUR_MS, aggregate_hourly, write_csv
from backtest_v13_hourly_reversion import run_window
from strategy_v9 import indicators

CHANNEL_HOURS = 24
WARMUP_HOURS = 48
SWEEP_ATR = 0.10
MAX_SWEEP_ATR = 2.0
MIN_CHANNEL_ATR = 3.0
MAX_CHANNEL_ATR = 12.0
STOP_ATR = 1.5  # V13 engine


def signals(hour):
    """Detect wick through prior 24h extreme, then close back inside."""
    result = [None] * len(hour)
    values = indicators(hour)
    streak = 1
    for i in range(1, len(hour)):
        streak = streak + 1 if hour[i]["timestamp"] - hour[i - 1]["timestamp"] == HOUR_MS else 1
        if streak < WARMUP_HOURS or i < CHANNEL_HOURS or values[i] is None:
            continue
        atr = values[i]["atr"]
        if atr <= 0:
            continue
        prior = hour[i - CHANNEL_HOURS:i]
        top = max(c["high"] for c in prior)
        bottom = min(c["low"] for c in prior)
        width = top - bottom
        if not MIN_CHANNEL_ATR * atr <= width <= MAX_CHANNEL_ATR * atr:
            continue
        c = hour[i]
        close = c["close"]
        middle = (top + bottom) / 2
        if (top + SWEEP_ATR * atr <= c["high"] <= top + MAX_SWEEP_ATR * atr
                and middle + STOP_ATR * atr <= close < top
                and close < c["open"]):
            result[i] = ("SHORT", atr, middle)
        elif (bottom - MAX_SWEEP_ATR * atr <= c["low"] <= bottom - SWEEP_ATR * atr
              and bottom < close <= middle - STOP_ATR * atr
              and close > c["open"]):
            result[i] = ("LONG", atr, middle)
    return result


def main():
    parser = argparse.ArgumentParser(description="V17 historical failed-breakout reversal, no orders")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "ADAUSDT"])
    parser.add_argument("--train-days", type=int, default=180)
    parser.add_argument("--test-days", type=int, default=90)
    args = parser.parse_args()
    if args.train_days < 30 or args.test_days < 30:
        parser.error("Each window must be >= 30 days")
    print("V17 FEHLAUSBRUCH – NUR HISTORISCH, KEINE ORDERS", flush=True)
    print("24h Hoch/Tief kurz ueberschritten, 1h schliesst zurueck in Range; "
          "naechste Stunde Einstieg, 1.5 ATR Stop, Mitte der Range Ziel, max 18h.", flush=True)
    print("Taker/Slippage beidseitig, adverse Funding 0.01% je 8h; "
          "TEST wurde in vorigen Strategieanalysen schon gesehen.", flush=True)
    for symbol in [s.upper() for s in args.symbols]:
        print("\n" + symbol, flush=True)
        try:
            five = load_csv(symbol, "5m")
        except FileNotFoundError as exc:
            print("Fehlende Daten:", exc, flush=True)
            continue
        if len(five) < 3000 or any(b["timestamp"] <= a["timestamp"] for a, b in zip(five, five[1:])):
            print("Zu wenige oder doppelte Daten.", flush=True)
            continue
        hour = aggregate_hourly(five)
        end = int(five[-1]["timestamp"] // HOUR_MS * HOUR_MS) - HOUR_MS
        split = end - args.test_days * DAY_MS
        start = split - args.train_days * DAY_MS
        if not hour or hour[0]["timestamp"] > start - WARMUP_HOURS * HOUR_MS:
            print("Zu wenig Historie fuer Warmup.", flush=True)
            continue
        print("5m:", timestamp_text(five[0]["timestamp"]), "bis",
              timestamp_text(five[-1]["timestamp"]), flush=True)
        setup = signals(hour)
        for label, lo, hi, days in (("TRAIN", start, split - HOUR_MS, args.train_days),
                                    ("TEST", split, end, args.test_days)):
            covered = sum(lo <= c["timestamp"] <= hi for c in hour)
            print(f"{label} Stunden: {covered}/{days * 24}", flush=True)
            if covered < days * 24 * .90:
                print(f"{label} Daten unvollstaendig; kein Ergebnis.", flush=True)
                continue
            r = run_window(hour, setup, lo, hi)
            print(f"{label} | Trades {r['count']} | L/S {r['longs']}/{r['shorts']} | "
                  f"Treffer {r['win_rate']:.1f}% | PF {r['pf']:.2f} | "
                  f"Netto {r['net']:+.2f} USDT | Gebuehren {r['fees']:.2f} | "
                  f"Funding-Annahme {r['funding']:.2f} | "
                  f"Realisiert-DD {r['realized_dd_pct']:.2f}%", flush=True)
            write_csv(Path("v17_results") / f"{symbol}_{label.lower()}.csv", r)
    print("FERTIG – CSVs in v17_results; keine Live-Aenderung.", flush=True)


if __name__ == "__main__":
    main()
