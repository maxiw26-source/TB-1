"""Research only: 1h Bollinger mean reversion in a quiet ADX regime.

Frozen first-pass rules, next hourly open, stop-first OHLC, taker/slippage
both sides and adverse fixed funding. Never connects to an order API.
"""
import argparse
from pathlib import Path

from backtest_v8_walkforward import load_csv
from backtest_v9 import (LEVERAGE, MAX_MARGIN_PCT, RISK_PCT, SLIPPAGE_PCT,
                         START_BALANCE, TAKER_FEE_PCT, close_position,
                         timestamp_text)
from backtest_v12_hourly_trend import (DAY_MS, HOUR_MS, aggregate_hourly,
                                       funding_cost, write_csv)
from strategy_v9 import PROFILE, indicators

STOP_ATR = 1.5
MIN_REWARD_RISK = 1.0
MAX_HOLD_HOURS = 18
ADX_MAX = 25.0
RSI_LONG = 40.0
RSI_SHORT = 60.0
WARMUP_HOURS = 50


def signals(hourly):
    """Only closed 1h candles; reject indicators after any data gap."""
    params = {**PROFILE, "vwap_period": 20, "bb_period": 20,
              "bb_sigma": 2.0}
    features = indicators(hourly, params)
    result = [None] * len(hourly)
    consecutive = 1
    for i in range(1, len(hourly)):
        consecutive = consecutive + 1 if (hourly[i]["timestamp"] -
                                            hourly[i - 1]["timestamp"] == HOUR_MS) else 1
        f = features[i]
        if consecutive < WARMUP_HOURS or f is None or f["atr"] <= 0 or f["adx"] > ADX_MAX:
            continue
        close = float(hourly[i]["close"])
        mean = f["bb_mid"]
        # Demand a wide enough move to offset a 1.5-ATR stop and costs.
        if close < f["bb_lower"] and f["rsi"] <= RSI_LONG and mean - close >= STOP_ATR * f["atr"]:
            result[i] = ("LONG", f["atr"], mean)
        elif close > f["bb_upper"] and f["rsi"] >= RSI_SHORT and close - mean >= STOP_ATR * f["atr"]:
            result[i] = ("SHORT", f["atr"], mean)
    return result


def open_trade(setup, raw_open, balance, ts):
    side, atr, target = setup
    d = 1 if side == "LONG" else -1
    entry = raw_open * (1 + d * SLIPPAGE_PCT / 100)
    distance = STOP_ATR * atr
    if entry <= 0 or distance <= 0 or balance <= 0 or d * (target - entry) < MIN_REWARD_RISK * distance:
        return None
    qty = min(balance * RISK_PCT / 100 / distance,
              balance * MAX_MARGIN_PCT / 100 * LEVERAGE / entry)
    if qty <= 0:
        return None
    return {"side": side, "entry": entry, "stop": entry - d * distance,
            "target": target, "qty": qty, "entry_ts": ts,
            "entry_fee": entry * qty * TAKER_FEE_PCT / 100, "bars": 0}


def exit_hit(position, candle):
    op, hi, lo = (float(candle[k]) for k in ("open", "high", "low"))
    stop, target = position["stop"], position["target"]
    if position["side"] == "LONG":
        if lo <= stop:
            return min(op, stop), "STOP"
        if hi >= target:
            return target, "MEAN"
    else:
        if hi >= stop:
            return max(op, stop), "STOP"
        if lo <= target:
            return target, "MEAN"
    if position["bars"] >= MAX_HOLD_HOURS:
        return float(candle["close"]), "TIME"
    return None


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
        dd = max(dd, 100 * (peak - balance) / peak if peak > 0 else 0)
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
            position = open_trade(pending, float(c["open"]), balance, ts)
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


def main():
    parser = argparse.ArgumentParser(description="V13 historical 1h mean reversion, no orders")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "ADAUSDT"])
    parser.add_argument("--train-days", type=int, default=180)
    parser.add_argument("--test-days", type=int, default=90)
    args = parser.parse_args()
    if args.train_days < 30 or args.test_days < 30:
        parser.error("train-days and test-days must be >= 30")
    print("V13 1H MEAN REVERSION – NUR HISTORISCH, KEINE ORDERS", flush=True)
    print("Bollinger 20/2, RSI 40/60, ADX <=25; 1.5 ATR Stop, Mittelband-Ziel, max 18h; "
          "Taker/Slippage beide Seiten, 0.01% angenommene Funding-Kosten je 8h.", flush=True)
    print("TEST ist durch fruehere Strategieversuche bereits eingesehen; kein unabhaengiger Holdout.", flush=True)
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
            print("Zu wenig Historie fuer TRAIN und Warmup.", flush=True)
            continue
        print("5m:", timestamp_text(five[0]["timestamp"]), "bis",
              timestamp_text(five[-1]["timestamp"]), flush=True)
        setups = signals(hour)
        for label, lo, hi, days in (("TRAIN", start, split - HOUR_MS, args.train_days),
                                    ("TEST", split, end, args.test_days)):
            available = sum(lo <= c["timestamp"] <= hi for c in hour)
            print(f"{label} Stunden: {available}/{days * 24}", flush=True)
            if available < days * 24 * 0.90:
                print(f"{label} Daten unvollstaendig; kein Ergebnis.", flush=True)
                continue
            r = run_window(hour, setups, lo, hi)
            print(f"{label} | Trades {r['count']} | L/S {r['longs']}/{r['shorts']} | "
                  f"Treffer {r['win_rate']:.1f}% | Trade-PF {r['pf']:.2f} | "
                  f"Netto {r['net']:+.2f} USDT | Gebuehren {r['fees']:.2f} | "
                  f"Funding-Annahme {r['funding']:.2f} | "
                  f"Realisiert-DD {r['realized_dd_pct']:.2f}%", flush=True)
            write_csv(Path("v13_results") / f"{symbol}_{label.lower()}.csv", r)
    print("FERTIG – CSVs in v13_results; keine Live-Aenderung.", flush=True)


if __name__ == "__main__":
    main()
