"""V12 research-only 1h Donchian trend, built from complete 5m bars.

Fixed parameters; next-hour-open entry, stop-first OHLC execution, market
costs on both sides and an assumed adverse funding charge. No order API.
"""
import argparse
import csv
from pathlib import Path

from backtest_v8_walkforward import load_csv
from backtest_v9 import (
    LEVERAGE, MAX_MARGIN_PCT, RISK_PCT, SLIPPAGE_PCT, START_BALANCE,
    TAKER_FEE_PCT, close_position, print_result, timestamp_text,
)
from backtest_v10_pullback import ema_series
from strategy_v9 import indicators

FIVE_MS = 300_000
HOUR_MS = 3_600_000
DAY_MS = 86_400_000
CHANNEL = 55
EMA_TREND = 200
STOP_ATR = 2.0
TARGET_R = 3.0
MAX_HOLD_HOURS = 24
FUNDING_INTERVAL_MS = 8 * HOUR_MS
ASSUMED_FUNDING_PCT = 0.01  # adverse charge per 8h; not historical rates


def aggregate_hourly(five):
    """Only aligned groups of twelve consecutive 5m bars make one 1h bar."""
    output = []
    bucket = None
    group = []
    for c in five:
        ts = int(c["timestamp"])
        key = ts - ts % HOUR_MS
        if key != bucket:
            bucket, group = key, []
        group.append(c)
        if len(group) == 12 and [int(b["timestamp"]) for b in group] == [
                key + j * FIVE_MS for j in range(12)]:
            output.append({"timestamp": key, "open": float(group[0]["open"]),
                           "high": max(float(b["high"]) for b in group),
                           "low": min(float(b["low"]) for b in group),
                           "close": float(group[-1]["close"]),
                           "volume": sum(float(b.get("volume") or 0) for b in group)})
    return output


def signals(hourly):
    """Use completed bar and past channel; never forward-fill across gaps."""
    ema = ema_series(hourly, EMA_TREND)
    features = indicators(hourly)
    result = [None] * len(hourly)
    consecutive = 1
    for i in range(1, len(hourly)):
        consecutive = consecutive + 1 if (hourly[i]["timestamp"] -
                                            hourly[i - 1]["timestamp"] == HOUR_MS) else 1
        if consecutive < EMA_TREND or i < CHANNEL or features[i] is None:
            continue
        atr = float(features[i]["atr"])
        if atr <= 0:
            continue
        # Exclude yesterday's stale channel values after gaps.
        top = max(float(b["high"]) for b in hourly[i - CHANNEL:i])
        bottom = min(float(b["low"]) for b in hourly[i - CHANNEL:i])
        close = float(hourly[i]["close"])
        prior = float(hourly[i - 1]["close"])
        if prior <= top < close <= top + atr and close > ema[i]:
            result[i] = ("LONG", atr)
        elif prior >= bottom > close >= bottom - atr and close < ema[i]:
            result[i] = ("SHORT", atr)
    return result


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
    d = 1 if side == "LONG" else -1
    return {"side": side, "entry": entry, "stop": entry - d * distance,
            "target": entry + d * TARGET_R * distance, "qty": qty,
            "entry_ts": ts, "entry_fee": entry * qty * TAKER_FEE_PCT / 100,
            "bars": 0}


def exit_hit(position, candle):
    op, hi, lo = (float(candle[k]) for k in ("open", "high", "low"))
    stop, target = position["stop"], position["target"]
    if position["side"] == "LONG":
        if lo <= stop:
            return min(op, stop), "STOP"
        if hi >= target:
            return target, "TARGET"
    else:
        if hi >= stop:
            return max(op, stop), "STOP"
        if lo <= target:
            return target, "TARGET"
    if position["bars"] >= MAX_HOLD_HOURS:
        return float(candle["close"]), "TIME"
    return None


def funding_cost(position, exit_ts):
    """Assume adverse funding at UTC 00:00/08:00/16:00 during holding.

    Exit at an hourly bar's close can cross the next boundary. Charge that
    boundary too, conservatively; use fixed entry notional as approximation.
    """
    entry = int(position["entry_ts"])
    exit_close = int(exit_ts) + HOUR_MS
    first = (entry // FUNDING_INTERVAL_MS + 1) * FUNDING_INTERVAL_MS
    periods = max(0, (exit_close - first) // FUNDING_INTERVAL_MS + 1)
    return periods * position["entry"] * position["qty"] * ASSUMED_FUNDING_PCT / 100


def run_window(hourly, setups, start, end):
    balance = peak = START_BALANCE
    dd = 0.0
    trades = []
    position = pending = None
    last = None

    def settle(raw, reason, ts):
        nonlocal position, balance, peak, dd
        trade = close_position(position, raw, reason, ts)
        trade["funding_cost"] = funding_cost(position, ts)
        trade["net"] -= trade["funding_cost"]
        trades.append(trade)
        balance += trade["net"]
        peak = max(balance, peak)
        dd = max(dd, (peak - balance) / peak * 100 if peak > 0 else 0)
        position = None

    for i, c in enumerate(hourly):
        ts = int(c["timestamp"])
        if not start <= ts <= end:
            continue
        last = c
        gap = i == 0 or ts - int(hourly[i - 1]["timestamp"]) != HOUR_MS
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
            position = open_trade(pending[0], float(c["open"]), pending[1], balance, ts)
            if position is not None:
                position["bars"] = 1
                hit = exit_hit(position, c)
                if hit:
                    settle(*hit, ts)
        pending = setups[i] if position is None and ts + HOUR_MS <= end else None
    if position is not None:
        settle(float(last["close"]), "WINDOW_END", int(last["timestamp"]))
    gains = sum(t["net"] for t in trades if t["net"] > 0)
    losses = -sum(t["net"] for t in trades if t["net"] <= 0)
    return {"trades": trades, "count": len(trades),
            "longs": sum(t["side"] == "LONG" for t in trades),
            "shorts": sum(t["side"] == "SHORT" for t in trades),
            "win_rate": 100 * sum(t["net"] > 0 for t in trades) / len(trades) if trades else 0,
            "pf": gains / losses if losses else (float("inf") if gains else 0),
            "net": balance - START_BALANCE,
            "fees": sum(t["entry_fee"] + t["exit_fee"] for t in trades),
            "funding": sum(t["funding_cost"] for t in trades),
            "realized_dd_pct": dd}


def write_csv(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ("side", "entry_ts", "exit_ts", "entry", "exit", "qty", "gross",
              "entry_fee", "exit_fee", "funding_cost", "net", "reason")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(result["trades"])


def main():
    parser = argparse.ArgumentParser(description="V12 historical hourly trend only")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "ADAUSDT"])
    parser.add_argument("--train-days", type=int, default=180)
    parser.add_argument("--test-days", type=int, default=90)
    args = parser.parse_args()
    if args.train_days < 30 or args.test_days < 30:
        parser.error("train-days and test-days must be >= 30")
    print("V12 1H TREND – NUR HISTORISCH, KEINE ORDERS", flush=True)
    print("55h Kanal-Ausbruch + EMA200; 2 ATR Stop, 3R Ziel, max. 24h. "
          "Taker/Slippage beide Seiten; Funding-Annahme 0.01% je 8h (immer Kosten).", flush=True)
    print("TEST-Zeitraeume wurden bereits in vorherigen Strategieversuchen angesehen.", flush=True)
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
        if len(hour) <= EMA_TREND:
            print("Zu wenige vollstaendige Stunden.", flush=True)
            continue
        end = int(five[-1]["timestamp"] // HOUR_MS * HOUR_MS) - HOUR_MS
        split = end - args.test_days * DAY_MS
        start = split - args.train_days * DAY_MS
        print("5m:", timestamp_text(five[0]["timestamp"]), "bis",
              timestamp_text(five[-1]["timestamp"]), flush=True)
        if hour[0]["timestamp"] > start - EMA_TREND * HOUR_MS:
            print("Zu wenig Historie fuer TRAIN und 200h Warmup.", flush=True)
            continue
        setups = signals(hour)
        for label, lo, hi, days in (("TRAIN", start, split - HOUR_MS, args.train_days),
                                    ("TEST ", split, end, args.test_days)):
            available = sum(lo <= c["timestamp"] <= hi for c in hour)
            print(f"{label} Stunden: {available}/{days * 24}", flush=True)
            if available < days * 24 * 0.90:
                print(f"{label} Daten unvollstaendig; kein Ergebnis.", flush=True)
                continue
            result = run_window(hour, setups, lo, hi)
            print_result(label, result)
            print(f"{label} Funding-Annahme: {result['funding']:.2f} USDT", flush=True)
            write_csv(Path("v12_results") / f"{symbol}_{label.strip().lower()}.csv", result)
    print("FERTIG – CSVs in v12_results; keine Live-Aenderung.", flush=True)


if __name__ == "__main__":
    main()
