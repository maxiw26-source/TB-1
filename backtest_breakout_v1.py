import csv
import os
from bisect import bisect_right

import config
from strategy_breakout_v1 import calculate_breakout_signal


DATA_FOLDER = "historical_data"
SYMBOLS = ["BTCUSDT", "ETHUSDT"]

BACKTEST_DAYS = 1500

START_BALANCE = float(
    getattr(config, "START_BALANCE", 10000.0)
)

RISK_PERCENT = float(
    getattr(config, "RISK_PER_TRADE_PERCENT", 0.25)
)

LEVERAGE = float(
    getattr(config, "LEVERAGE", 3)
)

MAX_MARGIN_PERCENT = float(
    getattr(config, "MAX_MARGIN_PERCENT", 20)
)

MAKER_FEE_PERCENT = float(
    getattr(config, "MAKER_FEE_PERCENT", 0.02)
)

TAKER_FEE_PERCENT = float(
    getattr(config, "TAKER_FEE_PERCENT", 0.06)
)

TAKER_SLIPPAGE_PERCENT = float(
    getattr(config, "TAKER_SLIPPAGE_PERCENT", 0.02)
)

MAX_COST_TO_RISK_RATIO = float(
    getattr(config, "MAX_COST_TO_RISK_RATIO", 0.12)
)


def load_csv(symbol, interval):
    path = os.path.join(
        DATA_FOLDER,
        f"{symbol}_{interval}.csv",
    )

    candles = []

    with open(
        path,
        "r",
        encoding="utf-8",
        newline="",
    ) as file:
        reader = csv.DictReader(file)

        for row in reader:
            candles.append({
                "timestamp": int(row["timestamp"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(
                    row.get("volume", 0) or 0
                ),
            })

    candles.sort(
        key=lambda candle: candle["timestamp"]
    )

    return candles

def history_before(
    candles,
    candle_times,
    timestamp,
    limit=300,
):
    end_index = bisect_right(
        candle_times,
        timestamp,
    )

    start_index = max(
        0,
        end_index - limit,
    )

    return candles[start_index:end_index]

def maker_fee(price, quantity):
    return (
        price
        * quantity
        * MAKER_FEE_PERCENT
        / 100
    )


def taker_fee(price, quantity):
    return (
        price
        * quantity
        * TAKER_FEE_PERCENT
        / 100
    )


def taker_slippage(price, quantity):
    return (
        price
        * quantity
        * TAKER_SLIPPAGE_PERCENT
        / 100
    )

def create_position(
    side,
    entry,
    stop,
    tp1,
    tp2,
    balance,
    timestamp,
):
    risk_distance = (
        entry - stop
        if side == "LONG"
        else stop - entry
    )

    if risk_distance <= 0:
        return None

    risk_amount = (
        balance
        * RISK_PERCENT
        / 100
    )

    risk_quantity = (
        risk_amount
        / risk_distance
    )

    max_notional = (
        balance
        * MAX_MARGIN_PERCENT
        / 100
        * LEVERAGE
    )

    max_quantity = (
        max_notional
        / entry
    )

    quantity = min(
        risk_quantity,
        max_quantity,
    )

    if quantity <= 0:
        return None

    estimated_costs = (
        maker_fee(entry, quantity)
        + taker_fee(entry, quantity)
        + taker_slippage(entry, quantity)
    )

    actual_risk = (
        risk_distance
        * quantity
    )

    if actual_risk <= 0:
        return None

    cost_ratio = (
        estimated_costs
        / actual_risk
    )

    if cost_ratio > MAX_COST_TO_RISK_RATIO:
        return None

    return {
        "side": side,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "quantity": quantity,
        "remaining": quantity,
        "tp1_hit": False,
        "gross_pnl": 0.0,
        "fees": maker_fee(
            entry,
            quantity,
        ),
        "slippage": 0.0,
        "entry_timestamp": timestamp,
        "last_exit": entry,
        "reason": "",
        "cost_ratio": cost_ratio,
    }

def exit_part(
    position,
    price,
    quantity,
    reason,
):
    quantity = min(
        quantity,
        position["remaining"],
    )

    if quantity <= 0:
        return

    if position["side"] == "LONG":
        pnl = (
            price - position["entry"]
        ) * quantity
    else:
        pnl = (
            position["entry"] - price
        ) * quantity

    position["gross_pnl"] += pnl

    if reason == "STOP":
        position["fees"] += taker_fee(
            price,
            quantity,
        )

        position["slippage"] += taker_slippage(
            price,
            quantity,
        )
    else:
        position["fees"] += maker_fee(
            price,
            quantity,
        )

    position["remaining"] -= quantity

    if position["remaining"] < 1e-12:
        position["remaining"] = 0.0

    position["last_exit"] = price
    position["reason"] = reason

def check_position(
    position,
    candle,
):
    high = float(candle["high"])
    low = float(candle["low"])

    if position["side"] == "LONG":
        if low <= position["stop"]:
            exit_part(
                position,
                position["stop"],
                position["remaining"],
                "STOP",
            )
            return True

        if (
            not position["tp1_hit"]
            and high >= position["tp1"]
        ):
            exit_part(
                position,
                position["tp1"],
                position["quantity"] * 0.5,
                "TP1",
            )

            position["tp1_hit"] = True
            position["stop"] = position["entry"]

        if (
            high >= position["tp2"]
            and position["remaining"] > 0
        ):
            exit_part(
                position,
                position["tp2"],
                position["remaining"],
                "TP2",
            )
            return True

    else:
        if high >= position["stop"]:
            exit_part(
                position,
                position["stop"],
                position["remaining"],
                "STOP",
            )
            return True

        if (
            not position["tp1_hit"]
            and low <= position["tp1"]
        ):
            exit_part(
                position,
                position["tp1"],
                position["quantity"] * 0.5,
                "TP1",
            )

            position["tp1_hit"] = True
            position["stop"] = position["entry"]

        if (
            low <= position["tp2"]
            and position["remaining"] > 0
        ):
            exit_part(
                position,
                position["tp2"],
                position["remaining"],
                "TP2",
            )
            return True

    return position["remaining"] <= 0

def finalize_position(
    position,
    timestamp,
):
    net_pnl = (
        position["gross_pnl"]
        - position["fees"]
        - position["slippage"]
    )

    return {
        "side": position["side"],
        "entry_timestamp": position["entry_timestamp"],
        "exit_timestamp": timestamp,
        "entry": position["entry"],
        "exit": position["last_exit"],
        "quantity": position["quantity"],
        "gross_pnl": position["gross_pnl"],
        "fees": position["fees"],
        "slippage": position["slippage"],
        "net_pnl": net_pnl,
        "reason": position["reason"],
        "tp1_hit": position["tp1_hit"],
        "cost_ratio": position["cost_ratio"],
    }

def run_backtest(symbol):
    entry_candles = load_csv(symbol, "1m")
    confirmation_candles = load_csv(symbol, "5m")
    trend_candles = load_csv(symbol, "15m")

    cutoff_timestamp = (
        entry_candles[-1]["timestamp"]
        - BACKTEST_DAYS * 24 * 60 * 60 * 1000
    )

    entry_candles = [
        candle
        for candle in entry_candles
        if candle["timestamp"] >= cutoff_timestamp
    ]

    confirmation_candles = [
        candle
        for candle in confirmation_candles
        if candle["timestamp"] >= cutoff_timestamp
    ]

    trend_candles = [
        candle
        for candle in trend_candles
        if candle["timestamp"] >= cutoff_timestamp
    ]

    confirmation_times = [
        candle["timestamp"]
        for candle in confirmation_candles
    ]

    trend_times = [
        candle["timestamp"]
        for candle in trend_candles
    ]

    signals = 0
    pending_breakout = None
    pending_expiry_candles = 10
    cooldown_until = -1
    cooldown_candles = 10

    balance = START_BALANCE
    position = None
    trades = []

    for index, candle in enumerate(entry_candles):
        timestamp = candle["timestamp"]
        if position is not None:
            if check_position(
                position,
                candle,
            ):
                trade = finalize_position(
                    position,
                    timestamp,
                )

                balance += trade["net_pnl"]
                trades.append(trade)

                position = None
                cooldown_until = (
                    index + cooldown_candles
                )

            continue
        if index <= cooldown_until:
            continue
        if pending_breakout is not None:
            age = index - pending_breakout["created_index"]

            if age > pending_expiry_candles:
                pending_breakout = None

            elif age >= 1:
                breakout = pending_breakout["breakout"]
                side = pending_breakout["side"]
                level = float(breakout["level"])

                low = float(candle["low"])
                high = float(candle["high"])
                close = float(candle["close"])

                tolerance = level * 0.10 / 100

                open_price = float(candle["open"])
                if side == "LONG":
                   retest_hit = (
                     low <= level + tolerance
                     and close > level * 1.0005
                     and close > open_price
    )
                else:
                   retest_hit = (
                    high >= level - tolerance
                     and close < level * 0.9995
                     and close > open_price
    )

                if retest_hit:
                    entry = close

                    if side == "LONG":
                       stop = min(
                           low,
                           level,
           )
                       risk_distance = entry - stop
                       tp1 = entry + risk_distance * 0.8
                       tp2 = entry + risk_distance * 1.5
                    else:
                       stop = max(
                       high,
                       level,
        )
                       risk_distance = stop - entry
                       tp1 = entry - risk_distance * 0.8
                       tp2 = entry - risk_distance * 1.5

                    new_position = create_position(
                         side,
                         entry,
                         stop,
                         tp1,
                         tp2,
                         balance,
                         timestamp,
    )

                    if new_position is not None:
                       position = new_position
                       signals += 1

                    pending_breakout = None


        entry_history = entry_candles[
            max(0, index - 300 + 1):
            index + 1
        ]

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

        result = calculate_breakout_signal(
            entry_history,
            confirmation_history,
            trend_history,
        )

        if result is None:
            continue

        indicators = result.get("indicators", {})
        breakout = indicators.get("breakout")

        if breakout is not None and pending_breakout is None:
            pending_breakout = {
               "side": indicators["side"],
               "breakout": breakout,
               "created_index": index,
    }


        winners = [
        trade
        for trade in trades
        if trade["net_pnl"] > 0
    ]

    losers = [
        trade
        for trade in trades
        if trade["net_pnl"] < 0
    ]

    total_profit = sum(
        trade["net_pnl"]
        for trade in winners
    )

    total_loss = abs(
        sum(
            trade["net_pnl"]
            for trade in losers
        )
    )

    if total_loss > 0:
        profit_factor = (
            total_profit
            / total_loss
        )

    elif total_profit > 0:
        profit_factor = float("inf")

    else:
        profit_factor = 0.0

    win_rate = (
        len(winners)
        / len(trades)
        * 100
        if trades
        else 0.0
    )

    print("")
    print("================================")
    print("BREAKOUT V1 BACKTEST")
    print("Symbol:", symbol)
    print("================================")

    print(
        "Startkapital:",
        round(START_BALANCE, 2),
        "USDT",
    )

    print(
        "Endkapital:",
        round(balance, 2),
        "USDT",
    )

    print(
        "Trades:",
        len(trades),
    )

    print(
        "Gewinner:",
        len(winners),
    )

    print(
        "Verlierer:",
        len(losers),
    )

    print(
        "Trefferquote:",
        round(win_rate, 2),
        "%",
    )

    print(
        "Profit-Faktor:",
        "unendlich"
        if profit_factor == float("inf")
        else round(profit_factor, 2),
    )

    print(
        "Netto-PnL:",
        round(
            balance - START_BALANCE,
            2,
        ),
        "USDT",
    )

    print(
        "Gebühren:",
        round(
            sum(
                trade["fees"]
                for trade in trades
            ),
            2,
        ),
        "USDT",
    )

    print(
        "Slippage:",
        round(
            sum(
                trade["slippage"]
                for trade in trades
            ),
            2,
        ),
        "USDT",
    )

    print(
        "Gültige Entries:",
        signals,
    )

    print("================================")


if __name__ == "__main__":
    for symbol in SYMBOLS:
        run_backtest(symbol)