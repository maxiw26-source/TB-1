"""Research-only V9 5m mean-reversion backtest. No exchange/order API.

Fixed first-pass settings (no parameter tuning using the test period).
Past-only signals, next-candle-open entry, conservative intrabar stop-first,
market taker fees and slippage on entry AND exit, ATR stop, VWAP target,
and a time exit. Funding and imperfect real fills are not modeled.
"""
import argparse
import csv
from datetime import datetime, timezone
from pathlib import Path

import config
from backtest_v8_walkforward import load_csv
from strategy_v9 import PROFILE, indicators, signal

START_BALANCE = float(getattr(config, "START_BALANCE", 10000.0))
RISK_PCT = 0.25
LEVERAGE = 3.0
MAX_MARGIN_PCT = 20.0
TAKER_FEE_PCT = float(getattr(config, "TAKER_FEE_PERCENT", 0.06))
SLIPPAGE_PCT = float(getattr(config, "TAKER_SLIPPAGE_PERCENT", 0.02))
CANDLE_MS = 300_000


def timestamp_text(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).isoformat()


def open_position(side, open_price, atr, vwap, capital, timestamp):
    slip = SLIPPAGE_PCT / 100.0
    entry = open_price * (1 + slip if side == "LONG" else 1 - slip)
    risk_distance = PROFILE["stop_atr"] * atr
    if entry <= 0 or risk_distance <= 0:
        return None
    stop = entry - risk_distance if side == "LONG" else entry + risk_distance
    risk_to_target = (
        (vwap - entry) / risk_distance
        if side == "LONG"
        else (entry - vwap) / risk_distance
    )
    if risk_to_target < PROFILE["minimum_reward_risk"]:
        return None
    qty = min(
        capital * RISK_PCT / 100.0 / risk_distance,
        capital * MAX_MARGIN_PCT / 100.0 * LEVERAGE / entry,
    )
    if qty <= 0:
        return None
    return {
        "side": side,
        "entry": entry,
        "stop": stop,
        "target": vwap,
        "qty": qty,
        "entry_ts": timestamp,
        "entry_fee": entry * qty * TAKER_FEE_PCT / 100.0,
        "bars": 0,
    }


def close_position(position, raw_exit, reason, timestamp):
    side = position["side"]
    # Market-exit slippage applies even for stops/TP in this conservative
    # simplified model. Real fill prices may be worse in fast markets.
    slip = SLIPPAGE_PCT / 100.0
    exit_price = raw_exit * (1 - slip if side == "LONG" else 1 + slip)
    qty = position["qty"]
    gross = (
        (exit_price - position["entry"]) * qty
        if side == "LONG"
        else (position["entry"] - exit_price) * qty
    )
    exit_fee = exit_price * qty * TAKER_FEE_PCT / 100.0
    net = gross - position["entry_fee"] - exit_fee
    return {
        "side": side,
        "entry_ts": position["entry_ts"],
        "exit_ts": timestamp,
        "entry": position["entry"],
        "exit": exit_price,
        "qty": qty,
        "gross": gross,
        "entry_fee": position["entry_fee"],
        "exit_fee": exit_fee,
        "net": net,
        "reason": reason,
    }


def evaluate_exit(position, candle):
    """Conservative stop-first when stop and target both touch same 5m bar."""
    op = float(candle["open"])
    hi = float(candle["high"])
    lo = float(candle["low"])
    stop = position["stop"]
    target = position["target"]
    if position["side"] == "LONG":
        if lo <= stop:
            return min(op, stop), "STOP"
        if hi >= target:
            return target, "VWAP"
    else:
        if hi >= stop:
            return max(op, stop), "STOP"
        if lo <= target:
            return target, "VWAP"
    if position["bars"] >= PROFILE["max_hold_bars"]:
        return float(candle["close"]), "TIME"
    return None


def run_window(candles, features, start_ts, end_ts):
    capital = START_BALANCE
    peak = capital
    worst_dd = 0.0
    trades = []
    position = None
    candidate = None
    cooldown_until = -1

    # Precomputed indicators use trailing bars only. The oldest 7 days
    # preceding this window can warm up indicators but cannot create trades.
    for i, candle in enumerate(candles):
        ts = int(candle["timestamp"])
        if ts < start_ts or ts > end_ts:
            continue

        previous = candles[i - 1] if i else None
        gap = (
            previous is None
            or ts <= previous["timestamp"]
            or ts - previous["timestamp"] > 2 * CANDLE_MS
        )
        if gap:
            candidate = None

        if position is not None:
            position["bars"] += 1
            hit = evaluate_exit(position, candle)
            if hit:
                raw_exit, reason = hit
                trade = close_position(position, raw_exit, reason, ts)
                trades.append(trade)
                capital += trade["net"]
                peak = max(peak, capital)
                worst_dd = max(
                    worst_dd,
                    (peak - capital) / peak * 100.0 if peak > 0 else 0.0,
                )
                position = None
                cooldown_until = i + 3

        # A signal on bar i-1 can only enter at bar i OPEN.
        if position is None and candidate is not None and not gap and i > cooldown_until:
            position = open_position(
                candidate["side"],
                float(candle["open"]),
                candidate["atr"],
                candidate["vwap"],
                capital,
                ts,
            )
            candidate = None
            if position is not None:
                # Entry-bar stop/target can be hit; conservative stop first.
                position["bars"] = 1
                hit = evaluate_exit(position, candle)
                if hit:
                    raw_exit, reason = hit
                    trade = close_position(position, raw_exit, reason, ts)
                    trades.append(trade)
                    capital += trade["net"]
                    peak = max(peak, capital)
                    worst_dd = max(
                        worst_dd,
                        (peak - capital) / peak * 100.0 if peak > 0 else 0.0,
                    )
                    position = None
                    cooldown_until = i + 3

        # Don't skip the candle after a closure for an earlier pending
        # setup: a new signal is allowed only after cooldown.
        if position is None and i > cooldown_until and features[i] is not None:
            side = signal(candle, features[i])
            if side is not None:
                candidate = {
                    "side": side,
                    "atr": features[i]["atr"],
                    "vwap": features[i]["vwap"],
                }
        else:
            candidate = None

    # Never count an unrealized open position as a closed profit.
    if position is not None:
        last = next(c for c in reversed(candles) if start_ts <= c["timestamp"] <= end_ts)
        trade = close_position(position, float(last["close"]), "WINDOW_END", last["timestamp"])
        trades.append(trade)
        capital += trade["net"]
        peak = max(peak, capital)
        worst_dd = max(worst_dd, (peak - capital) / peak * 100.0 if peak > 0 else 0.0)

    winners = [t for t in trades if t["net"] > 0]
    profit = sum(t["net"] for t in winners)
    loss = -sum(t["net"] for t in trades if t["net"] <= 0)
    return {
        "trades": trades,
        "count": len(trades),
        "longs": sum(t["side"] == "LONG" for t in trades),
        "shorts": sum(t["side"] == "SHORT" for t in trades),
        "win_rate": len(winners) / len(trades) * 100.0 if trades else 0.0,
        "pf": profit / loss if loss else (float("inf") if profit else 0.0),
        "net": capital - START_BALANCE,
        "fees": sum(t["entry_fee"] + t["exit_fee"] for t in trades),
        "realized_dd_pct": worst_dd,
    }


def print_result(label, result):
    print(
        f"{label} | Trades {result['count']} | L/S "
        f"{result['longs']}/{result['shorts']} | "
        f"Treffer {result['win_rate']:.1f}% | "
        f"Trade-PF {result['pf']:.2f} | "
        f"Netto {result['net']:+.2f} USDT | "
        f"Gebuehren {result['fees']:.2f} | "
        f"Realisiert-DD {result['realized_dd_pct']:.2f}%",
        flush=True,
    )


def write_trades(path, result):
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "side", "entry_ts", "exit_ts", "entry", "exit", "qty",
        "gross", "entry_fee", "exit_fee", "net", "reason",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(result["trades"])


def main():
    parser = argparse.ArgumentParser(description="V9 mean-reversion research backtest")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "ADAUSDT"])
    parser.add_argument("--train-days", type=int, default=240)
    parser.add_argument("--test-days", type=int, default=120)
    args = parser.parse_args()
    if args.train_days < 30 or args.test_days < 30:
        parser.error("Train/test muessen jeweils >= 30 Tage sein")

    print("V9 MEAN REVERSION – NUR BACKTEST, KEINE ECHTEN ORDERS", flush=True)
    print(
        "5m Bollinger(20,2) + RSI(14) + 4h VWAP + ADX-Regime; "
        "ATR-Stop und VWAP-Ziel. Feste V1-Regeln, keine Optimierung.",
        flush=True,
    )
    print(
        "Gebuehren + Entry/Exit-Slippage enthalten. "
        "Funding, echte Orderbuch-Fills und laufende "
        "unrealisierte Drawdowns nicht modelliert.",
        flush=True,
    )

    day_ms = 86_400_000
    for symbol in [s.upper() for s in args.symbols]:
        print("\nBerechne " + symbol + " ...", flush=True)
        candles = load_csv(symbol, "5m")
        if len(candles) < 500:
            print("Zu wenige 5m-Kerzen – uebersprungen.", flush=True)
            continue
        last = candles[-1]["timestamp"]
        test_start = last - args.test_days * day_ms
        train_start = test_start - args.train_days * day_ms
        if candles[0]["timestamp"] > train_start - 2 * day_ms:
            print(
                "Nicht genug Daten fuer Training + 2 Tage Indikator-Warmup; "
                "bitte historical_data.py mit mehr Tagen ausfuehren.",
                flush=True,
            )
            continue
        series = indicators(candles)
        train = run_window(candles, series, train_start, test_start - CANDLE_MS)
        test = run_window(candles, series, test_start, last)
        print_result("TRAIN", train)
        print_result("TEST (zeitlich spaeter)", test)
        write_trades(Path("v9_results") / f"{symbol}_train.csv", train)
        write_trades(Path("v9_results") / f"{symbol}_test.csv", test)
    print("\nFERTIG. Ergebnisse in v9_results/ (keine Live-Aenderung).", flush=True)


if __name__ == "__main__":
    main()
