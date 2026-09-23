"""V10 research-only trend continuation on a 5m EMA20 pullback.

Fixed first-pass hypothesis: closed 15m EMA200 + ADX(14) >= 20 define
trend; 5m EMA20/EMA50 alignment and a close back across EMA20 trigger.
Enter at the NEXT 5m open. ATR(14) stop 1.5x, target 2R, time exit
after 24 bars. Taker fees and slippage both sides, stop-first within a
bar. Funding, spreads, market impact and unrealized drawdown are absent.
No live trading imports or API calls. Existing V7/V8/V9 remain untouched.
"""
import argparse
from pathlib import Path

from backtest_v8_walkforward import load_csv
from backtest_v9 import (
    CANDLE_MS, LEVERAGE, MAX_MARGIN_PCT, RISK_PCT, SLIPPAGE_PCT,
    START_BALANCE, TAKER_FEE_PCT, close_position, print_result, timestamp_text,
    write_trades,
)
from strategy_v9 import indicators

FIFTEEN_MS = 900_000
ADX_MIN = 20.0
STOP_ATR = 1.5
TARGET_R = 2.0
HOLD_BARS = 24
COOLDOWN_BARS = 3


def ema_series(candles, period):
    result = [None] * len(candles)
    if len(candles) < period:
        return result
    closes = [float(c["close"]) for c in candles]
    value = sum(closes[:period]) / period
    result[period - 1] = value
    alpha = 2.0 / (period + 1.0)
    for i in range(period, len(candles)):
        value += alpha * (closes[i] - value)
        result[i] = value
    return result


def trend_at_5m(candles_5m, candles_15m):
    """Only fully closed 15m bars; gaps and stale data block signals."""
    if not candles_15m:
        return [None] * len(candles_5m)
    times = [int(c["timestamp"]) for c in candles_15m]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("15m timestamps must be unique and ascending")
    ema200 = ema_series(candles_15m, 200)
    adx = indicators(candles_15m)
    consecutive = [1] * len(times)
    for i in range(1, len(times)):
        if times[i] - times[i - 1] == FIFTEEN_MS:
            consecutive[i] = consecutive[i - 1] + 1
    result = [None] * len(candles_5m)
    j = -1
    for i, candle in enumerate(candles_5m):
        closed_at = int(candle["timestamp"]) + CANDLE_MS
        while j + 1 < len(times) and times[j + 1] + FIFTEEN_MS <= closed_at:
            j += 1
        if j < 0 or consecutive[j] < 200 or adx[j] is None:
            continue
        if closed_at >= times[j] + 2 * FIFTEEN_MS:
            continue
        if adx[j]["adx"] < ADX_MIN:
            continue
        close = float(candles_15m[j]["close"])
        result[i] = "LONG" if close > ema200[j] else "SHORT" if close < ema200[j] else None
    return result


def setup_side(candles, i, ema20, ema50, values, trend):
    if i < 1 or trend[i] is None or values[i] is None:
        return None
    if ema20[i - 1] is None or ema50[i] is None or values[i]["atr"] <= 0:
        return None
    if int(candles[i]["timestamp"]) - int(candles[i - 1]["timestamp"]) != CANDLE_MS:
        return None
    prev, current = candles[i - 1], candles[i]
    close, op, prior = float(current["close"]), float(current["open"]), float(prev["close"])
    distance = abs(close - ema20[i])
    if distance > 0.5 * values[i]["atr"]:
        return None
    if (trend[i] == "LONG" and ema20[i] > ema50[i]
            and prior <= ema20[i - 1] and close > ema20[i] and close > op):
        return "LONG"
    if (trend[i] == "SHORT" and ema20[i] < ema50[i]
            and prior >= ema20[i - 1] and close < ema20[i] and close < op):
        return "SHORT"
    return None


def open_trade(side, raw_open, atr, capital, timestamp):
    slip = SLIPPAGE_PCT / 100.0
    entry = raw_open * (1 + slip if side == "LONG" else 1 - slip)
    risk = STOP_ATR * atr
    if entry <= 0 or risk <= 0 or capital <= 0:
        return None
    qty = min(capital * RISK_PCT / 100.0 / risk,
              capital * MAX_MARGIN_PCT / 100.0 * LEVERAGE / entry)
    if qty <= 0:
        return None
    direction = 1 if side == "LONG" else -1
    return {"side": side, "entry": entry, "stop": entry - direction * risk,
            "target": entry + direction * TARGET_R * risk,
            "qty": qty, "entry_ts": timestamp,
            "entry_fee": entry * qty * TAKER_FEE_PCT / 100.0, "bars": 0}


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


def run_window(candles, values, ema20, ema50, trend, start, end):
    capital = peak = START_BALANCE
    dd = 0.0
    trades = []
    position = pending = None
    cooldown = -1
    for i, c in enumerate(candles):
        ts = int(c["timestamp"])
        if not start <= ts <= end:
            continue
        gap = i == 0 or ts - int(candles[i - 1]["timestamp"]) != CANDLE_MS
        if gap:
            pending = None
        if position is not None:
            position["bars"] += 1
            hit = exit_hit(position, c)
            if hit:
                t = close_position(position, hit[0], hit[1], ts)
                trades.append(t)
                capital += t["net"]
                peak = max(peak, capital)
                dd = max(dd, (peak - capital) / peak * 100.0)
                position = None
                cooldown = i + COOLDOWN_BARS
        if position is None and pending is not None and not gap and i > cooldown:
            position = open_trade(pending["side"], float(c["open"]),
                                  pending["atr"], capital, ts)
            pending = None
            if position is not None:
                position["bars"] = 1
                hit = exit_hit(position, c)
                if hit:
                    t = close_position(position, hit[0], hit[1], ts)
                    trades.append(t)
                    capital += t["net"]
                    peak = max(peak, capital)
                    dd = max(dd, (peak - capital) / peak * 100.0)
                    position = None
                    cooldown = i + COOLDOWN_BARS
        if position is None and i > cooldown:
            side = setup_side(candles, i, ema20, ema50, values, trend)
            pending = {"side": side, "atr": values[i]["atr"]} if side else None
        else:
            pending = None
    if position is not None:
        last = next(c for c in reversed(candles) if start <= c["timestamp"] <= end)
        t = close_position(position, float(last["close"]), "WINDOW_END", last["timestamp"])
        trades.append(t)
        capital += t["net"]
        peak = max(peak, capital)
        dd = max(dd, (peak - capital) / peak * 100.0)
    gains = sum(t["net"] for t in trades if t["net"] > 0)
    losses = -sum(t["net"] for t in trades if t["net"] <= 0)
    return {"trades": trades, "count": len(trades),
            "longs": sum(t["side"] == "LONG" for t in trades),
            "shorts": sum(t["side"] == "SHORT" for t in trades),
            "win_rate": sum(t["net"] > 0 for t in trades) / len(trades) * 100 if trades else 0,
            "pf": gains / losses if losses else (float("inf") if gains else 0),
            "net": capital - START_BALANCE,
            "fees": sum(t["entry_fee"] + t["exit_fee"] for t in trades),
            "realized_dd_pct": dd}


def main():
    parser = argparse.ArgumentParser(description="V10 pullback research backtest only")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "ADAUSDT"])
    parser.add_argument("--train-days", type=int, default=240)
    parser.add_argument("--test-days", type=int, default=120)
    args = parser.parse_args()
    if args.train_days < 30 or args.test_days < 30:
        parser.error("train/test must each be at least 30 days")
    print("V10 TREND-PULLBACK – NUR BACKTEST, KEINE ORDERS", flush=True)
    print("15m EMA200 + ADX14 >= 20; 5m EMA20-Rueckkehr/EMA50; SL 1.5 ATR, TP 2R.", flush=True)
    print("Taker-Gebuehren und Slippage enthalten; Funding/Fills nicht modelliert.", flush=True)
    print("Historische TEST-Fenster wurden in frueheren Strategietests bereits betrachtet.", flush=True)
    day = 86_400_000
    for symbol in [s.upper() for s in args.symbols]:
        print(f"\n{symbol}", flush=True)
        try:
            candles = load_csv(symbol, "5m")
            higher = load_csv(symbol, "15m")
        except FileNotFoundError as exc:
            print(f"Fehlende Daten: {exc}", flush=True)
            continue
        if len(candles) < 500 or len(higher) < 200:
            print("Zu wenige historische Kerzen.", flush=True)
            continue
        end = int(candles[-1]["timestamp"])
        split = end - args.test_days * day
        start = split - args.train_days * day
        print("5m:", timestamp_text(candles[0]["timestamp"]), "bis",
              timestamp_text(candles[-1]["timestamp"]), flush=True)
        print("15m:", timestamp_text(higher[0]["timestamp"]), "bis",
              timestamp_text(higher[-1]["timestamp"]), flush=True)
        if (int(candles[0]["timestamp"]) > start
                or int(higher[0]["timestamp"]) > start):
            print("Historische Daten beginnen nach dem Train-Start; uebersprungen.", flush=True)
            continue
        values = indicators(candles)
        e20, e50 = ema_series(candles, 20), ema_series(candles, 50)
        trend = trend_at_5m(candles, higher)
        for label, lo, hi in (("TRAIN", start, split - CANDLE_MS),
                              ("TEST ", split, end)):
            indexes = [i for i, c in enumerate(candles) if lo <= c["timestamp"] <= hi]
            valid = sum(trend[i] is not None for i in indexes)
            setups = sum(setup_side(candles, i, e20, e50, values, trend) is not None
                         for i in indexes)
            share = valid / len(indexes) * 100 if indexes else 0.0
            print(f"{label} Daten: {len(indexes)} 5m-Kerzen, {share:.1f}% mit "
                  f"gueltigem 15m-Trend, {setups} Roheinstiege", flush=True)
            if share < 90.0:
                print(f"{label} unvollstaendige Indikatorabdeckung; kein Ergebnis.", flush=True)
                continue
            result = run_window(candles, values, e20, e50, trend, lo, hi)
            print_result(label, result)
            write_trades(Path("v10_results") / f"{symbol}_{label.strip().lower()}.csv", result)
    print("FERTIG – CSVs in v10_results; keine Live-Aenderung.", flush=True)


if __name__ == "__main__":
    main()
