import csv
import os
from bisect import bisect_right

import config
from strategy_v7 import calculate_signal

DATA_FOLDER = "historical_data"
SYMBOL = "BTCUSDT"
START_BALANCE = float(getattr(config, "START_BALANCE", 10000.0))
CANDLE_LIMIT = max(int(getattr(config, "CANDLE_LIMIT", 300)), 220)

ADX_PERIOD = int(getattr(config, "ADX_PERIOD", 14))
EMA_TREND_PERIOD = int(getattr(config, "EMA_TREND_PERIOD", 200))
RISK_PERCENT = float(getattr(config, "RISK_PER_TRADE_PERCENT", 0.25))
LEVERAGE = float(getattr(config, "LEVERAGE", 3))
MAX_MARGIN_PERCENT = float(getattr(config, "MAX_MARGIN_PERCENT", 20))
COOLDOWN_CANDLES = int(getattr(config, "COOLDOWN_CANDLES", 10))
SETUP_EXPIRY_CANDLES = int(getattr(config, "SETUP_EXPIRY_CANDLES", 5))
TRADING_FEE_PERCENT = float(getattr(config, "TRADING_FEE_PERCENT", 0.06))
SLIPPAGE_PERCENT = float(getattr(config, "SLIPPAGE_PERCENT", 0.02))
TP1_R = float(getattr(config, "TP1_R_MULTIPLE", 1.0))
TP2_R = float(getattr(config, "TP2_R_MULTIPLE", 2.0))
TP3_R = float(getattr(config, "TP3_R_MULTIPLE", 3.0))


def load_csv(symbol, interval):
    path = os.path.join(DATA_FOLDER, f"{symbol}_{interval}.csv")
    candles = []
    with open(path, "r", encoding="utf-8", newline="") as file:
        for row in csv.DictReader(file):
            candles.append({
                "timestamp": int(row["timestamp"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0) or 0),
            })
    candles.sort(key=lambda candle: candle["timestamp"])
    return candles


def history_before(candles, times, timestamp):
    end = bisect_right(times, timestamp)
    return candles[max(0, end - CANDLE_LIMIT):end]


def fee(price, quantity):
    return price * quantity * TRADING_FEE_PERCENT / 100


def slippage(price, quantity):
    return price * quantity * SLIPPAGE_PERCENT / 100


def create_position(setup, balance, timestamp):
    side = setup["side"]
    entry = float(setup["entry"])
    stop = float(setup["stop"])
    risk_distance = entry - stop if side == "LONG" else stop - entry

    if risk_distance <= 0:
        return None

    risk_amount = balance * RISK_PERCENT / 100
    risk_quantity = risk_amount / risk_distance
    max_notional = balance * MAX_MARGIN_PERCENT / 100 * LEVERAGE
    quantity = min(risk_quantity, max_notional / entry)

    if quantity <= 0:
        return None

    return {
        "side": side,
        "entry": entry,
        "stop": stop,
        "tp1": entry + risk_distance * TP1_R if side == "LONG" else entry - risk_distance * TP1_R,
        "tp2": entry + risk_distance * TP2_R if side == "LONG" else entry - risk_distance * TP2_R,
        "tp3": entry + risk_distance * TP3_R if side == "LONG" else entry - risk_distance * TP3_R,
        "quantity": quantity,
        "remaining": quantity,
        "tp1_hit": False,
        "tp2_hit": False,
        "gross_pnl": 0.0,
        "fees": fee(entry, quantity),
        "slippage": slippage(entry, quantity),
        "entry_timestamp": int(timestamp),
        "last_exit": entry,
        "reason": "",
    }


def exit_part(position, price, quantity, reason):
    quantity = min(quantity, position["remaining"])
    if quantity <= 0:
        return

    pnl = (
        (price - position["entry"]) * quantity
        if position["side"] == "LONG"
        else (position["entry"] - price) * quantity
    )

    position["gross_pnl"] += pnl
    position["fees"] += fee(price, quantity)
    position["slippage"] += slippage(price, quantity)
    position["remaining"] -= quantity
    position["last_exit"] = price
    position["reason"] = reason


def check_position(position, candle):
    high = float(candle["high"])
    low = float(candle["low"])

    if position["side"] == "LONG":
        if low <= position["stop"]:
            exit_part(position, position["stop"], position["remaining"], "STOP")
            return True
        if not position["tp1_hit"] and high >= position["tp1"]:
            exit_part(position, position["tp1"], position["quantity"] * 0.50, "TP1")
            position["tp1_hit"] = True
            position["stop"] = position["entry"]
        if not position["tp2_hit"] and high >= position["tp2"]:
            exit_part(position, position["tp2"], position["quantity"] * 0.25, "TP2")
            position["tp2_hit"] = True
        if high >= position["tp3"] and position["remaining"] > 0:
            exit_part(position, position["tp3"], position["remaining"], "TP3")
            return True
    else:
        if high >= position["stop"]:
            exit_part(position, position["stop"], position["remaining"], "STOP")
            return True
        if not position["tp1_hit"] and low <= position["tp1"]:
            exit_part(position, position["tp1"], position["quantity"] * 0.50, "TP1")
            position["tp1_hit"] = True
            position["stop"] = position["entry"]
        if not position["tp2_hit"] and low <= position["tp2"]:
            exit_part(position, position["tp2"], position["quantity"] * 0.25, "TP2")
            position["tp2_hit"] = True
        if low <= position["tp3"] and position["remaining"] > 0:
            exit_part(position, position["tp3"], position["remaining"], "TP3")
            return True

    return position["remaining"] <= 0


def finalize(position, timestamp):
    return {
        "side": position["side"],
        "entry_timestamp": position["entry_timestamp"],
        "exit_timestamp": int(timestamp),
        "entry": position["entry"],
        "exit": position["last_exit"],
        "quantity": position["quantity"],
        "gross_pnl": position["gross_pnl"],
        "fees": position["fees"],
        "slippage": position["slippage"],
        "net_pnl": position["gross_pnl"] - position["fees"] - position["slippage"],
        "reason": position["reason"],
    }


def run_backtest():
    entry_candles = load_csv(SYMBOL, "1m")
    confirmation_candles = load_csv(SYMBOL, "5m")
    trend_candles = load_csv(SYMBOL, "15m")

    confirmation_times = [c["timestamp"] for c in confirmation_candles]
    trend_times = [c["timestamp"] for c in trend_candles]

    balance = START_BALANCE
    peak_balance = START_BALANCE
    max_drawdown = 0.0
    position = None
    pending = None
    trades = []
    used_setups = set()
    cooldown_until = -1

    signal_checks = 0
    pending_created = 0
    pending_filled = 0
    pending_expired = 0
    pending_invalid = 0
    duplicates = 0

    for index, candle in enumerate(entry_candles):
        timestamp = candle["timestamp"]

        if position is not None:
            if check_position(position, candle):
                trade = finalize(position, timestamp)
                balance += trade["net_pnl"]
                trades.append(trade)
                position = None
                cooldown_until = index + COOLDOWN_CANDLES
                peak_balance = max(peak_balance, balance)
                if peak_balance > 0:
                    max_drawdown = max(max_drawdown, (peak_balance - balance) / peak_balance * 100)
            continue

        if pending is not None:
            setup = pending["setup"]
            age = index - pending["created_index"]

            invalid = (
                float(candle["low"]) <= setup["stop"]
                if setup["side"] == "LONG"
                else float(candle["high"]) >= setup["stop"]
            )

            if invalid:
                pending = None
                pending_invalid += 1
                continue

            if age > SETUP_EXPIRY_CANDLES:
                pending = None
                pending_expired += 1
                continue

            entry = setup["entry"]
            if float(candle["low"]) <= entry <= float(candle["high"]):
                position = create_position(setup, balance, timestamp)
                if position is not None:
                    pending_filled += 1
                pending = None
                continue

        if index <= cooldown_until:
            continue

        entry_history = entry_candles[max(0, index - CANDLE_LIMIT + 1):index + 1]
        confirmation_history = history_before(confirmation_candles, confirmation_times, timestamp)
        trend_history = history_before(trend_candles, trend_times, timestamp)

        if len(confirmation_history) < ADX_PERIOD * 2 + 2:
            continue
        if len(trend_history) < EMA_TREND_PERIOD:
            continue

        result = calculate_signal(entry_history, confirmation_history, trend_history)
        signal_checks += 1

        if result.get("signal") not in ("PENDING_LONG", "PENDING_SHORT"):
            continue

        indicators = result.get("indicators", {})
        setup_id = indicators.get("setup_id")
        setup = indicators.get("setup")

        if setup_id is None or setup is None:
            continue

        if setup_id in used_setups:
            duplicates += 1
            continue

        used_setups.add(setup_id)
        pending = {
            "setup_id": setup_id,
            "setup": setup,
            "created_index": index,
        }
        pending_created += 1

    if position is not None:
        last = entry_candles[-1]
        exit_part(position, last["close"], position["remaining"], "BACKTEST ENDE")
        trade = finalize(position, last["timestamp"])
        balance += trade["net_pnl"]
        trades.append(trade)

    winners = [trade for trade in trades if trade["net_pnl"] > 0]
    losers = [trade for trade in trades if trade["net_pnl"] < 0]
    profit = sum(trade["net_pnl"] for trade in winners)
    loss = abs(sum(trade["net_pnl"] for trade in losers))
    profit_factor = profit / loss if loss > 0 else float("inf") if profit > 0 else 0.0
    win_rate = len(winners) / len(trades) * 100 if trades else 0.0

    filename = f"backtest_v7_{SYMBOL}_trades.csv"
    fields = [
        "side", "entry_timestamp", "exit_timestamp", "entry", "exit",
        "quantity", "gross_pnl", "fees", "slippage", "net_pnl", "reason"
    ]
    with open(filename, "w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(trades)

    print("")
    print("================================")
    print("LSOB_V7 BACKTEST")
    print("Symbol:", SYMBOL)
    print("================================")
    print("Startkapital:", round(START_BALANCE, 2), "USDT")
    print("Endkapital:", round(balance, 2), "USDT")
    print("Trades:", len(trades))
    print("Gewinner:", len(winners))
    print("Verlierer:", len(losers))
    print("Trefferquote:", round(win_rate, 2), "%")
    print("Profit-Faktor:", "unendlich" if profit_factor == float("inf") else round(profit_factor, 2))
    print("Netto-PnL:", round(balance - START_BALANCE, 2), "USDT")
    print("Rendite:", round((balance - START_BALANCE) / START_BALANCE * 100, 2), "%")
    print("Max. Drawdown:", round(max_drawdown, 2), "%")
    print("Gebühren:", round(sum(t["fees"] for t in trades), 2), "USDT")
    print("Slippage:", round(sum(t["slippage"] for t in trades), 2), "USDT")
    print("Trade-Datei:", filename)
    print("")
    print("SETUP-AUSWERTUNG")
    print("Signalprüfungen:", signal_checks)
    print("Pending erstellt:", pending_created)
    print("Pending gefüllt:", pending_filled)
    print("Pending abgelaufen:", pending_expired)
    print("Pending ungültig:", pending_invalid)
    print("Doppelte Setups blockiert:", duplicates)
    print("================================")


if __name__ == "__main__":
    run_backtest()
