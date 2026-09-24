"""V11 research: first UTC hour range break, 5m volume confirmation.

Uses only completed candles for signals and enters at the following open.
Single entry per UTC day, conservative stop-first intrabar exit. No orders.
"""
import argparse
from pathlib import Path

from backtest_v8_walkforward import load_csv
from backtest_v9 import (
    CANDLE_MS, LEVERAGE, MAX_MARGIN_PCT, RISK_PCT, SLIPPAGE_PCT,
    START_BALANCE, TAKER_FEE_PCT, close_position, print_result,
    timestamp_text, write_trades,
)
from strategy_v9 import indicators

DAY_MS = 86_400_000
RANGE_BARS = 12
VOLUME_BARS = 20
VOLUME_MULTIPLIER = 1.5
STOP_ATR = 1.5
TARGET_R = 2.0
HOLD_BARS = 36


def opening_ranges(candles):
    """Require all twelve aligned, consecutive bars from 00:00 to 00:55 UTC."""
    groups = {}
    for c in candles:
        ts = int(c["timestamp"])
        day = ts // DAY_MS
        if ts % DAY_MS < RANGE_BARS * CANDLE_MS:
            groups.setdefault(day, []).append(c)
    ranges = {}
    for day, bars in groups.items():
        if (len(bars) == RANGE_BARS and
                [int(c["timestamp"]) for c in bars] ==
                [day * DAY_MS + j * CANDLE_MS for j in range(RANGE_BARS)]):
            ranges[day] = (max(float(c["high"]) for c in bars),
                           min(float(c["low"]) for c in bars))
    return ranges


def signal_at(candles, i, values, ranges):
    """Return signal and ATR using the current CLOSED 5m bar and earlier bars."""
    if i < VOLUME_BARS or values[i] is None:
        return None
    bar = candles[i]
    ts = int(bar["timestamp"])
    within = ts % DAY_MS
    if not RANGE_BARS * CANDLE_MS <= within < 6 * 60 * 60 * 1000:
        return None
    day = ts // DAY_MS
    if day not in ranges:
        return None
    # No signals immediately after a missing candle or from stale volume.
    if any(int(candles[j]["timestamp"]) - int(candles[j - 1]["timestamp"]) != CANDLE_MS
           for j in range(i - VOLUME_BARS + 1, i + 1)):
        return None
    atr = float(values[i]["atr"])
    top, bottom = ranges[day]
    if atr <= 0 or not 1.0 <= (top - bottom) / atr <= 8.0:
        return None
    avg_vol = sum(float(candles[j].get("volume") or 0)
                  for j in range(i - VOLUME_BARS, i)) / VOLUME_BARS
    if avg_vol <= 0 or float(bar.get("volume") or 0) < avg_vol * VOLUME_MULTIPLIER:
        return None
    prior = float(candles[i - 1]["close"])
    close = float(bar["close"])
    if prior <= top < close <= top + atr:
        return "LONG", atr
    if prior >= bottom > close >= bottom - atr:
        return "SHORT", atr
    return None


def open_trade(side, raw_open, atr, capital, ts):
    entry = raw_open * (1 + SLIPPAGE_PCT / 100 if side == "LONG" else
                        1 - SLIPPAGE_PCT / 100)
    distance = STOP_ATR * atr
    if entry <= 0 or distance <= 0 or capital <= 0:
        return None
    qty = min(capital * RISK_PCT / 100 / distance,
              capital * MAX_MARGIN_PCT / 100 * LEVERAGE / entry)
    if qty <= 0:
        return None
    direction = 1 if side == "LONG" else -1
    return {"side": side, "entry": entry, "stop": entry - direction * distance,
            "target": entry + direction * TARGET_R * distance,
            "qty": qty, "entry_ts": ts,
            "entry_fee": entry * qty * TAKER_FEE_PCT / 100, "bars": 0}


def exit_hit(position, candle):
    op, high, low = (float(candle[k]) for k in ("open", "high", "low"))
    stop, target = position["stop"], position["target"]
    if position["side"] == "LONG":
        if low <= stop:
            return min(op, stop), "STOP"
        if high >= target:
            return target, "TARGET"
    else:
        if high >= stop:
            return max(op, stop), "STOP"
        if low <= target:
            return target, "TARGET"
    if position["bars"] >= HOLD_BARS:
        return float(candle["close"]), "TIME"
    return None


def run_window(candles, values, ranges, start, end):
    capital = peak = START_BALANCE
    worst_dd = 0.0
    trades = []
    position = pending = None
    used_days = set()
    last_candle = None
    def settle(raw_exit, reason, ts):
        nonlocal capital, peak, worst_dd, position
        trade = close_position(position, raw_exit, reason, ts)
        trades.append(trade)
        capital += trade["net"]
        peak = max(peak, capital)
        worst_dd = max(worst_dd, (peak - capital) / peak * 100 if peak > 0 else 0)
        position = None

    for i, c in enumerate(candles):
        ts = int(c["timestamp"])
        if not start <= ts <= end:
            continue
        last_candle = c
        gap = i == 0 or ts - int(candles[i - 1]["timestamp"]) != CANDLE_MS
        if gap:
            pending = None
            if position is not None:
                settle(float(c["open"]), "GAP", ts)
        if position is not None:
            position["bars"] += 1
            hit = exit_hit(position, c)
            if hit:
                settle(*hit, ts)
        if position is None and pending is not None and not gap:
            position = open_trade(pending["side"], float(c["open"]),
                                  pending["atr"], capital, ts)
            if position is not None:
                used_days.add(pending["day"])
                position["bars"] = 1
                hit = exit_hit(position, c)
                if hit:
                    settle(*hit, ts)
        pending = None
        day = ts // DAY_MS
        if position is None and day not in used_days and ts + CANDLE_MS <= end:
            setup = signal_at(candles, i, values, ranges)
            if setup is not None and (ts + CANDLE_MS) // DAY_MS == day:
                pending = {"side": setup[0], "atr": setup[1], "day": day}
    if position is not None:
        settle(float(last_candle["close"]), "WINDOW_END", int(last_candle["timestamp"]))
    gains = sum(t["net"] for t in trades if t["net"] > 0)
    losses = -sum(t["net"] for t in trades if t["net"] <= 0)
    return {"trades": trades, "count": len(trades),
            "longs": sum(t["side"] == "LONG" for t in trades),
            "shorts": sum(t["side"] == "SHORT" for t in trades),
            "win_rate": sum(t["net"] > 0 for t in trades) / len(trades) * 100 if trades else 0,
            "pf": gains / losses if losses else (float("inf") if gains else 0),
            "net": capital - START_BALANCE,
            "fees": sum(t["entry_fee"] + t["exit_fee"] for t in trades),
            "realized_dd_pct": worst_dd}


def main():
    parser = argparse.ArgumentParser(description="V11 UTC opening range, historical only")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "ADAUSDT"])
    parser.add_argument("--train-days", type=int, default=180)
    parser.add_argument("--test-days", type=int, default=90)
    args = parser.parse_args()
    if args.train_days < 30 or args.test_days < 30:
        parser.error("train-days and test-days must each be >= 30")
    print("V11 UTC OPENING RANGE – NUR HISTORISCHER BACKTEST, KEINE ORDERS", flush=True)
    print("00:00–01:00 UTC Range; 01:00–06:00 Ausbruch, Volumen >= 1.5x; "
          "SL 1.5 ATR, TP 2R, max. 3h.", flush=True)
    print("Taker-Gebuehren und Slippage auf beiden Seiten; Funding/echte Fills "
          "nicht modelliert. TEST-Daten wurden in frueheren Versuchen bereits betrachtet.", flush=True)
    for symbol in [s.upper() for s in args.symbols]:
        print(f"\n{symbol}", flush=True)
        try:
            candles = load_csv(symbol, "5m")
        except FileNotFoundError as exc:
            print(f"Fehlende Daten: {exc}", flush=True)
            continue
        if len(candles) < 500 or any(int(b["timestamp"]) <= int(a["timestamp"])
                                     for a, b in zip(candles, candles[1:])):
            print("Zu wenige oder doppelte 5m-Kerzen.", flush=True)
            continue
        end = int(candles[-1]["timestamp"])
        split = end - args.test_days * DAY_MS
        start = split - args.train_days * DAY_MS
        if int(candles[0]["timestamp"]) > start - VOLUME_BARS * CANDLE_MS:
            print("Zu wenig Historie fuer TRAIN und Indikator-Warmup.", flush=True)
            continue
        values = indicators(candles)
        ranges = opening_ranges(candles)
        print("Daten:", timestamp_text(candles[0]["timestamp"]), "bis",
              timestamp_text(end), "; vollstaendige UTC-Ranges:", len(ranges), flush=True)
        for label, lo, hi, days in (("TRAIN", start, split - CANDLE_MS, args.train_days),
                                    ("TEST ", split, end, args.test_days)):
            available = sum(lo <= c["timestamp"] <= hi for c in candles)
            usable_days = sum(lo <= day * DAY_MS <= hi for day in ranges)
            print(f"{label} Daten: {available}/{days * 288} 5m-Kerzen, "
                  f"{usable_days}/{days} vollstaendige Tages-Ranges", flush=True)
            if available < days * 288 * 0.90 or usable_days < days * 0.80:
                print(f"{label} unvollstaendige Daten; kein Ergebnis.", flush=True)
                continue
            result = run_window(candles, values, ranges, lo, hi)
            print_result(label, result)
            write_trades(Path("v11_results") / f"{symbol}_{label.strip().lower()}.csv", result)
    print("FERTIG – CSVs in v11_results; keine Live-Aenderung.", flush=True)


if __name__ == "__main__":
    main()
