import csv
import os
from bisect import bisect_right

import config
from strategy_breakout_v2 import (
    RETEST_EXPIRY_CANDLES,
    atr,
    build_retest_plan,
    calculate_breakout_candidate,
)

DATA_FOLDER = "historical_data"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
BACKTEST_DAYS = 365

START_BALANCE = float(getattr(config, "START_BALANCE", 10000.0))
CANDLE_LIMIT = max(int(getattr(config, "CANDLE_LIMIT", 300)), 220)
RISK_PERCENT = float(getattr(config, "RISK_PER_TRADE_PERCENT", 0.25))
LEVERAGE = float(getattr(config, "LEVERAGE", 3))
MAX_MARGIN_PERCENT = float(getattr(config, "MAX_MARGIN_PERCENT", 20.0))
MAKER_FEE_PERCENT = float(getattr(config, "MAKER_FEE_PERCENT", 0.02))
TAKER_FEE_PERCENT = float(getattr(config, "TAKER_FEE_PERCENT", 0.06))
TAKER_SLIPPAGE_PERCENT = float(getattr(config, "TAKER_SLIPPAGE_PERCENT", 0.02))
MAX_COST_TO_RISK_RATIO = float(getattr(config, "MAX_COST_TO_RISK_RATIO", 0.12))


def load_csv(symbol, interval):
    path = os.path.join(DATA_FOLDER, f"{symbol}_{interval}.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    candles = []
    with open(path, "r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            candles.append(
                {
                    "timestamp": int(row["timestamp"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume", 0) or 0),
                }
            )

    candles.sort(key=lambda c: c["timestamp"])
    return candles


def history_before(candles, times, timestamp):
    end_index = bisect_right(times, timestamp)
    start_index = max(0, end_index - CANDLE_LIMIT)
    return candles[start_index:end_index]


def maker_fee(price, quantity):
    return price * quantity * MAKER_FEE_PERCENT / 100.0


def taker_fee(price, quantity):
    return price * quantity * TAKER_FEE_PERCENT / 100.0


def taker_slippage(price, quantity):
    return price * quantity * TAKER_SLIPPAGE_PERCENT / 100.0


def create_position(plan, balance):
    entry = float(plan["entry"])
    stop = float(plan["stop"])
    risk_distance = abs(entry - stop)

    if risk_distance <= 0:
        return None

    risk_amount = balance * RISK_PERCENT / 100.0
    risk_quantity = risk_amount / risk_distance

    max_notional = balance * MAX_MARGIN_PERCENT / 100.0 * LEVERAGE
    max_quantity = max_notional / entry

    quantity = min(risk_quantity, max_quantity)
    if quantity <= 0:
        return None

    estimated_costs = (
        maker_fee(entry, quantity)
        + taker_fee(entry, quantity)
        + taker_slippage(entry, quantity)
    )
    actual_risk = risk_distance * quantity
    cost_ratio = estimated_costs / actual_risk if actual_risk > 0 else 999.0

    if cost_ratio > MAX_COST_TO_RISK_RATIO:
        return None

    return {
        **plan,
        "quantity": quantity,
        "remaining": quantity,
        "tp1_hit": False,
        "gross_pnl": 0.0,
        "fees": maker_fee(entry, quantity),
        "slippage": 0.0,
        "cost_ratio": cost_ratio,
        "reason": "",
        "last_exit": entry,
    }


def exit_part(position, price, quantity, reason):
    quantity = min(quantity, position["remaining"])
    if quantity <= 0:
        return

    if position["side"] == "LONG":
        pnl = (price - position["entry"]) * quantity
    else:
        pnl = (position["entry"] - price) * quantity

    position["gross_pnl"] += pnl

    if reason == "STOP":
        position["fees"] += taker_fee(price, quantity)
        position["slippage"] += taker_slippage(price, quantity)
    else:
        position["fees"] += maker_fee(price, quantity)

    position["remaining"] -= quantity
    if position["remaining"] < 1e-12:
        position["remaining"] = 0.0

    position["reason"] = reason
    position["last_exit"] = price


def check_position(position, candle):
    high = float(candle["high"])
    low = float(candle["low"])

    if position["side"] == "LONG":
        if low <= position["stop"]:
            exit_part(position, position["stop"], position["remaining"], "STOP")
            return True

        if not position["tp1_hit"] and high >= position["tp1"]:
            exit_part(position, position["tp1"], position["quantity"] * 0.5, "TP1")
            position["tp1_hit"] = True
            position["stop"] = position["entry"]

        if high >= position["tp2"] and position["remaining"] > 0:
            exit_part(position, position["tp2"], position["remaining"], "TP2")
            return True

    else:
        if high >= position["stop"]:
            exit_part(position, position["stop"], position["remaining"], "STOP")
            return True

        if not position["tp1_hit"] and low <= position["tp1"]:
            exit_part(position, position["tp1"], position["quantity"] * 0.5, "TP1")
            position["tp1_hit"] = True
            position["stop"] = position["entry"]

        if low <= position["tp2"] and position["remaining"] > 0:
            exit_part(position, position["tp2"], position["remaining"], "TP2")
            return True

    return position["remaining"] <= 0


def finalize(position, exit_timestamp):
    net_pnl = position["gross_pnl"] - position["fees"] - position["slippage"]
    return {
        "side": position["side"],
        "entry_timestamp": position["entry_timestamp"],
        "exit_timestamp": int(exit_timestamp),
        "net_pnl": net_pnl,
        "reason": position["reason"],
        "tp1_hit": position["tp1_hit"],
        "cost_ratio": position["cost_ratio"],
    }


def summarize(symbol, trades, start_balance, end_balance, breakout_count):
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    gross_profit = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")
    win_rate = 100.0 * len(wins) / len(trades) if trades else 0.0

    peak = start_balance
    balance = start_balance
    max_dd = 0.0
    for trade in trades:
        balance += trade["net_pnl"]
        peak = max(peak, balance)
        if peak > 0:
            max_dd = max(max_dd, (peak - balance) / peak * 100.0)

    print("")
    print(symbol)
    print("Breakouts:", breakout_count)
    print("Trades:", len(trades))
    print("Gewinner:", len(wins), "| Verlierer:", len(losses))
    print("Trefferquote: %.1f%%" % win_rate)
    print("Profit-Faktor:", "inf" if profit_factor == float("inf") else f"{profit_factor:.2f}")
    print("Netto PnL: %.2f USDT" % (end_balance - start_balance))
    print("Endstand: %.2f USDT" % end_balance)
    print("Max Drawdown: %.2f%%" % max_dd)


def run_backtest(symbol):
    entry_candles = load_csv(symbol, "1m")
    confirmation_candles = load_csv(symbol, "5m")
    trend_candles = load_csv(symbol, "15m")

    cutoff = entry_candles[-1]["timestamp"] - BACKTEST_DAYS * 24 * 60 * 60 * 1000
    entry_candles = [c for c in entry_candles if c["timestamp"] >= cutoff]
    confirmation_candles = [c for c in confirmation_candles if c["timestamp"] >= cutoff]
    trend_candles = [c for c in trend_candles if c["timestamp"] >= cutoff]

    confirmation_times = [c["timestamp"] for c in confirmation_candles]
    trend_times = [c["timestamp"] for c in trend_candles]

    balance = START_BALANCE
    trades = []
    position = None
    pending = None
    breakout_count = 0
    cooldown_until = -1

    for index, candle in enumerate(entry_candles):
        timestamp = candle["timestamp"]

        if position is not None:
            if check_position(position, candle):
                trade = finalize(position, timestamp)
                balance += trade["net_pnl"]
                trades.append(trade)
                position = None
                cooldown_until = index + 5
            continue

        if index <= cooldown_until:
            continue

        entry_history = entry_candles[max(0, index - CANDLE_LIMIT + 1): index + 1]
        confirmation_history = history_before(
            confirmation_candles,
            confirmation_times,
            timestamp,
        )
        trend_history = history_before(
            trend_candles,
            trend_times,
            timestamp,
        )

        current_atr = atr(entry_history, int(getattr(config, "ATR_PERIOD", 14)))

        if pending is not None:
            age = index - pending["created_index"]
            if age > RETEST_EXPIRY_CANDLES:
                pending = None
            elif current_atr is not None and age >= 1:
                plan = build_retest_plan(candle, pending, current_atr)
                if plan is not None:
                    new_position = create_position(plan, balance)
                    if new_position is not None:
                        position = new_position
                    pending = None
                    continue

        if pending is not None:
            continue

        result = calculate_breakout_candidate(
            entry_history,
            confirmation_history,
            trend_history,
        )

        if result.get("signal") is None:
            continue

        breakout = result["indicators"].get("breakout")
        if breakout is None:
            continue

        breakout_count += 1
        pending = {
            "side": breakout["side"],
            "level": float(breakout["level"]),
            "breakout_timestamp": int(breakout["breakout_timestamp"]),
            "created_index": index,
        }

    summarize(symbol, trades, START_BALANCE, balance, breakout_count)
    return {
        "symbol": symbol,
        "trades": trades,
        "net_pnl": balance - START_BALANCE,
        "end_balance": balance,
        "breakouts": breakout_count,
    }


def main():
    print("BREAKOUT V2 PAPER-BACKTEST")
    print("Zeitraum:", BACKTEST_DAYS, "Tage")
    print("Nur Simulation – keine Orders.")

    results = []
    for symbol in SYMBOLS:
        try:
            results.append(run_backtest(symbol))
        except FileNotFoundError as exc:
            print("")
            print(symbol, "- historische Datei fehlt:", exc)

    total_trades = sum(len(r["trades"]) for r in results)
    total_pnl = sum(r["net_pnl"] for r in results)

    print("")
    print("GESAMT")
    print("Trades:", total_trades)
    print("Netto PnL über Einzeltests: %.2f USDT" % total_pnl)


if __name__ == "__main__":
    main()
