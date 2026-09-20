# =====================================================# LSOB_V7 – backtest_v7.py
#
# Saubere Komplettversion
# - Multi-Timeframe
# - Pending FVG-Retracement
# - Risiko-/Margin-Begrenzung
# - Maker-/Taker-Gebühren
# - Slippage nur beim Stop
# - Kostenfilter
# - TP1 / TP2 / TP3
# - Break-even nach TP1
# - Setup- und Trade-Diagnose
# =====================================================
import csv
import os
from bisect import bisect_right

import config
from strategy_v7 import calculate_signal


DATA_FOLDER = "historical_data"
SYMBOLS = ["BTCUSDT", "ETHUSDT"]

BACKTEST_DAYS = 300

START_BALANCE = float(getattr(config, "START_BALANCE", 10000.0))
CANDLE_LIMIT = max(int(getattr(config, "CANDLE_LIMIT", 300)), 220)
ADX_PERIOD = int(getattr(config, "ADX_PERIOD", 14))
EMA_TREND_PERIOD = int(getattr(config, "EMA_TREND_PERIOD", 200))
RISK_PERCENT = float(getattr(config, "RISK_PER_TRADE_PERCENT", 0.25))
LEVERAGE = float(getattr(config, "LEVERAGE", 3))
MAX_MARGIN_PERCENT = float(getattr(config, "MAX_MARGIN_PERCENT", 20))
COOLDOWN_CANDLES = int(getattr(config, "COOLDOWN_CANDLES", 10))
SETUP_EXPIRY_CANDLES = int(getattr(config, "SETUP_EXPIRY_CANDLES", 5))
MAKER_FEE_PERCENT = float(getattr(config, "MAKER_FEE_PERCENT", 0.02))
TAKER_FEE_PERCENT = float(getattr(config, "TAKER_FEE_PERCENT", 0.06))
TAKER_SLIPPAGE_PERCENT = float(getattr(config, "TAKER_SLIPPAGE_PERCENT", 0.02))
TP1_R = float(getattr(config, "TP1_R_MULTIPLE", 1.0))
TP2_R = float(getattr(config, "TP2_R_MULTIPLE", 2.0))
TP3_R = float(getattr(config, "TP3_R_MULTIPLE", 3.0))
TP1_CLOSE_PERCENT = float(getattr(config, "TP1_CLOSE_PERCENT", 50.0))
TP2_CLOSE_PERCENT = float(getattr(config, "TP2_CLOSE_PERCENT", 25.0))
MAX_COST_TO_RISK_RATIO = float(getattr(config, "MAX_COST_TO_RISK_RATIO", 0.50))


def load_csv(symbol, interval):
    path = os.path.join(DATA_FOLDER, f"{symbol}_{interval}.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(f"Datei nicht gefunden: {path}")

    candles = []
    with open(path, "r", encoding="utf-8", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
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


def history_before(candles, candle_times, timestamp):
    end_index = bisect_right(candle_times, timestamp)
    start_index = max(0, end_index - CANDLE_LIMIT)
    return candles[start_index:end_index]


def maker_fee(price, quantity):
    return price * quantity * MAKER_FEE_PERCENT / 100


def taker_fee(price, quantity):
    return price * quantity * TAKER_FEE_PERCENT / 100


def taker_slippage(price, quantity):
    return price * quantity * TAKER_SLIPPAGE_PERCENT / 100


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
    max_quantity = max_notional / entry

    quantity = min(risk_quantity, max_quantity)
    if quantity <= 0:
        return None

    estimated_entry_fee = maker_fee(entry, quantity)
    estimated_exit_fee = taker_fee(entry, quantity)
    estimated_entry_slippage = 0.0
    estimated_exit_slippage = taker_slippage(entry, quantity)

    estimated_costs = (
        estimated_entry_fee
        + estimated_exit_fee
        + estimated_entry_slippage
        + estimated_exit_slippage
    )

    actual_risk = risk_distance * quantity
    if actual_risk <= 0:
        return None

    cost_ratio = estimated_costs / actual_risk
    if cost_ratio > MAX_COST_TO_RISK_RATIO:
        return None

    if side == "LONG":
        tp1 = entry + risk_distance * TP1_R
        tp2 = entry + risk_distance * TP2_R
        tp3 = entry + risk_distance * TP3_R
    else:
        tp1 = entry - risk_distance * TP1_R
        tp2 = entry - risk_distance * TP2_R
        tp3 = entry - risk_distance * TP3_R

    return {
        "side": side,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "tp3": tp3,
        "quantity": quantity,
        "remaining": quantity,
        "tp1_hit": False,
        "tp2_hit": False,
        "gross_pnl": 0.0,
        "fees": maker_fee(entry, quantity),
        "slippage": 0.0,
        "entry_timestamp": int(timestamp),
        "last_exit": entry,
        "reason": "",
        "cost_ratio": cost_ratio,
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

    if reason in ("STOP", "STOP-LOSS"):
        position["fees"] += taker_fee(price, quantity)
        position["slippage"] += taker_slippage(price, quantity)
    else:
        position["fees"] += maker_fee(price, quantity)

    position["remaining"] -= quantity

    if position["remaining"] < 1e-12:
        position["remaining"] = 0.0

    position["last_exit"] = float(price)
    position["reason"] = reason


def check_position(position, candle):
    high = float(candle["high"])
    low = float(candle["low"])

    if position["side"] == "LONG":
        if low <= position["stop"]:
            exit_part(position, position["stop"], position["remaining"], "STOP")
            return True

        if not position["tp1_hit"] and high >= position["tp1"]:
            exit_part(
                position,
                position["tp1"],
                position["quantity"] * TP1_CLOSE_PERCENT / 100,
                "TP1",
            )
            position["tp1_hit"] = True
            position["stop"] = position["entry"]


        if (
            not position["tp2_hit"]
            and high >= position["tp2"]
            and position["remaining"] > 0
        ):
            exit_part(
                position,
                position["tp2"],
                position["quantity"] * TP2_CLOSE_PERCENT / 100,
                "TP2",
            )
            position["tp2_hit"] = True
            position["stop"] = position["tp1"]

        if high >= position["tp3"] and position["remaining"] > 0:
            exit_part(position, position["tp3"], position["remaining"], "TP3")
            return True

    else:
        if high >= position["stop"]:
            exit_part(position, position["stop"], position["remaining"], "STOP")
            return True

        if not position["tp1_hit"] and low <= position["tp1"]:
            exit_part(
                position,
                position["tp1"],
                position["quantity"] * TP1_CLOSE_PERCENT / 100,
                "TP1",
            )
            position["tp1_hit"] = True
            position["stop"] = position["entry"]

        if (
            not position["tp2_hit"]
            and low <= position["tp2"]
            and position["remaining"] > 0
        ):
            exit_part(
                position,
                position["tp2"],
                position["quantity"] * TP2_CLOSE_PERCENT / 100,
                "TP2",
            )
            position["tp2_hit"] = True

        if low <= position["tp3"] and position["remaining"] > 0:
            exit_part(position, position["tp3"], position["remaining"], "TP3")
            return True

    return position["remaining"] <= 0


def finalize(position, timestamp):
    net_pnl = position["gross_pnl"] - position["fees"] - position["slippage"]

    return {
        "side": position["side"],
        "entry_timestamp": position["entry_timestamp"],
        "exit_timestamp": int(timestamp),
        "entry": position["entry"],
        "exit": position["last_exit"],
        "quantity": position["quantity"],
        "gross_pnl": position["gross_pnl"],
        "fees": position["fees"],
        "tp1_hit": position["tp1_hit"],
        "tp2_hit": position["tp2_hit"],
        "slippage": position["slippage"],
        "net_pnl": net_pnl,
        "reason": position["reason"],
        "cost_ratio": position["cost_ratio"],
    }


def print_trade_diagnostics(trades):
    if not trades:
        print("")
        print("DIAGNOSE")
        print("Keine Trades vorhanden.")
        return

    stop_trades = [
        trade for trade in trades
        if trade["reason"] in ("STOP", "STOP-LOSS")
    ]
    tp3_trades = [
        trade for trade in trades
        if trade["reason"] == "TP3"
    ]
    tp1_reached = [
        trade for trade in trades
        if trade.get("tp1_hit")
    ]
    tp2_reached = [
        trade for trade in trades
        if trade.get("tp2_hit")
    ]
    tp1_then_stop = [
        trade for trade in trades
        if trade.get("tp1_hit") and trade["reason"] in ("STOP", "STOP-LOSS")
    ]
    tp2_then_stop = [
        trade for trade in trades
        if trade.get("tp2_hit") and trade["reason"] in ("STOP", "STOP-LOSS")
    ]
    positive_gross_negative_net = [
        trade for trade in trades
        if trade["gross_pnl"] > 0 and trade["net_pnl"] < 0
    ]

    long_trades = [trade for trade in trades if trade["side"] == "LONG"]
    short_trades = [trade for trade in trades if trade["side"] == "SHORT"]

    long_wins = [trade for trade in long_trades if trade["net_pnl"] > 0]
    short_wins = [trade for trade in short_trades if trade["net_pnl"] > 0]

    average_gross = sum(trade["gross_pnl"] for trade in trades) / len(trades)
    average_net = sum(trade["net_pnl"] for trade in trades) / len(trades)
    average_fees = sum(trade["fees"] for trade in trades) / len(trades)
    average_slippage = sum(trade["slippage"] for trade in trades) / len(trades)
    average_cost_ratio = sum(trade["cost_ratio"] for trade in trades) / len(trades)

    long_net = sum(trade["net_pnl"] for trade in long_trades)
    short_net = sum(trade["net_pnl"] for trade in short_trades)

    long_win_rate = len(long_wins) / len(long_trades) * 100 if long_trades else 0.0
    short_win_rate = len(short_wins) / len(short_trades) * 100 if short_trades else 0.0

    print("")
    print("DIAGNOSE")
    print("Gesamt-Trades:", len(trades))
    print("Stop-Trades:", len(stop_trades))
    print("TP3-Trades:", len(tp3_trades))
    print("TP1 erreicht:", len(tp1_reached))
    print("TP2 erreicht:", len(tp2_reached))
    print("TP1 erreicht, dann Stop:", len(tp1_then_stop))
    print("TP2 erreicht, dann Stop:", len(tp2_then_stop))
    print("Brutto positiv, netto negativ:", len(positive_gross_negative_net))
    print("Ø Brutto-PnL pro Trade:", round(average_gross, 2), "USDT")
    print("Ø Netto-PnL pro Trade:", round(average_net, 2), "USDT")
    print("Ø Gebühren pro Trade:", round(average_fees, 2), "USDT")
    print("Ø Slippage pro Trade:", round(average_slippage, 2), "USDT")
    print("Ø Kosten/Risiko:", round(average_cost_ratio * 100, 2), "%")

    print("")
    print("RICHTUNGS-DIAGNOSE")
    print(
        "LONG Trades:",
        len(long_trades),
        "| Trefferquote:",
        round(long_win_rate, 2),
        "%",
        "| Netto:",
        round(long_net, 2),
        "USDT",
    )
    print(
        "SHORT Trades:",
        len(short_trades),
        "| Trefferquote:",
        round(short_win_rate, 2),
        "%",
        "| Netto:",
        round(short_net, 2),
        "USDT",
    )


def run_backtest():
def run_backtest(SYMBOL):
    entry_candles = load_csv(SYMBOL, "1m")
    confirmation_candles = load_csv(SYMBOL, "5m")
    trend_candles = load_csv(SYMBOL, "15m")

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

    confirmation_times = [candle["timestamp"] for candle in confirmation_candles]
    trend_times = [candle["timestamp"] for candle in trend_candles]

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
    rejected_by_cost = 0

    for index, candle in enumerate(entry_candles):
        if index % 100000 == 0:
            print(
                f"Fortschritt: {index:,} / {len(entry_candles):,} "
                f"({index / len(entry_candles) * 100:.1f} %)"
            )

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
                    current_drawdown = (peak_balance - balance) / peak_balance * 100
                    max_drawdown = max(max_drawdown, current_drawdown)
            continue

        if pending is not None:
            setup = pending["setup"]
            age = index - pending["created_index"]

            if setup["side"] == "LONG":
                invalid = float(candle["low"]) <= float(setup["stop"])
            else:
                invalid = float(candle["high"]) >= float(setup["stop"])

            if invalid:
                pending = None
                pending_invalid += 1
                continue

            if age > SETUP_EXPIRY_CANDLES:
                pending = None
                pending_expired += 1
                continue

            entry_price = float(setup["entry"])

            if float(candle["low"]) <= entry_price <= float(candle["high"]):
                new_position = create_position(setup, balance, timestamp)

                if new_position is not None:
                    position = new_position
                    pending_filled += 1
                else:
                    rejected_by_cost += 1

                pending = None
                continue

        if index <= cooldown_until:
            continue

        entry_history = entry_candles[
            max(0, index - CANDLE_LIMIT + 1):
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

        if len(confirmation_history) < ADX_PERIOD * 2 + 2:
            continue

        if len(trend_history) < EMA_TREND_PERIOD:
            continue

        result = calculate_signal(
            entry_history,
            confirmation_history,
            trend_history,
        )
        

        if SYMBOL == "ETHUSDT":
                    swing_lookback = 7
                    sweep_to_bos_candles = 12
                    bos_to_fvg_candles = 4
                    min_fvg_atr_ratio = 0.05
        else:
                    swing_lookback = 10
                    sweep_to_bos_candles = 8
                    bos_to_fvg_candles = 4
                    min_fvg_atr_ratio = 0.10

        

        if SYMBOL == "ETHUSDT":
                    swing_lookback = 7
        else:
                    swing_lookback = 10

        result = calculate_signal(
    entry_history,
    confirmation_history,
    trend_history,
    swing_lookback,
    sweep_to_bos_candles,
    bos_to_fvg_candles,
    min_fvg_atr_ratio,
)

        

        signal_checks += 1

        if result is None:
            continue
            continue

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
        last_candle = entry_candles[-1]
        exit_part(
            position,
            float(last_candle["close"]),
            position["remaining"],
            "BACKTEST ENDE",
        )
        trade = finalize(position, last_candle["timestamp"])
        balance += trade["net_pnl"]
        trades.append(trade)

    winners = [trade for trade in trades if trade["net_pnl"] > 0]
    losers = [trade for trade in trades if trade["net_pnl"] < 0]

    total_profit = sum(trade["net_pnl"] for trade in winners)
    total_loss = abs(sum(trade["net_pnl"] for trade in losers))

    if total_loss > 0:
        profit_factor = total_profit / total_loss
    elif total_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    win_rate = len(winners) / len(trades) * 100 if trades else 0.0

    result_file = f"backtest_v7_{SYMBOL}_trades.csv"
    fields = [
        "side",
        "entry_timestamp",
        "exit_timestamp",
        "entry",
        "exit",
        "quantity",
        "gross_pnl",
        "fees",
        "tp1_hit",
        "tp2_hit",
        "slippage",
        "net_pnl",
        "reason",
        "cost_ratio",
    ]

    with open(result_file, "w", encoding="utf-8", newline="") as file:
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
    print(
        "Profit-Faktor:",
        "unendlich" if profit_factor == float("inf") else round(profit_factor, 2),
    )
    print("Netto-PnL:", round(balance - START_BALANCE, 2), "USDT")
    print(
        "Rendite:",
        round((balance - START_BALANCE) / START_BALANCE * 100, 2),
        "%",
    )
    print("Max. Drawdown:", round(max_drawdown, 2), "%")
    print("Gebühren:", round(sum(trade["fees"] for trade in trades), 2), "USDT")
    print("Slippage:", round(sum(trade["slippage"] for trade in trades), 2), "USDT")
    print("Trade-Datei:", result_file)

    print("")
    print("SETUP-AUSWERTUNG")
    print("Signalprüfungen:", signal_checks)
    print("Pending erstellt:", pending_created)
    print("Pending gefüllt:", pending_filled)
    print("Pending abgelaufen:", pending_expired)
    print("Pending ungültig:", pending_invalid)
    print("Doppelte Setups blockiert:", duplicates)
    print("Wegen Kostenfilter abgelehnt:", rejected_by_cost)
    print("Max. Kosten/Risiko:", round(MAX_COST_TO_RISK_RATIO * 100, 1), "%")
    print("================================")

    print_trade_diagnostics(trades)

    return {
    "symbol": SYMBOL,
    "trades": len(trades),
    "winners": len(winners),
    "losers": len(losers),
    "net_pnl": balance - START_BALANCE,
    "profit_factor": profit_factor,
    "max_drawdown": max_drawdown,
    "fees": sum(trade["fees"] for trade in trades),
    "slippage": sum(trade["slippage"] for trade in trades),
}


if __name__ == "__main__":
    results = []

    for symbol in SYMBOLS:
        result = run_backtest(symbol)
        results.append(result)

    total_trades = sum(
        result["trades"]
        for result in results
    )

    total_net_pnl = sum(
        result["net_pnl"]
        for result in results
    )

    total_fees = sum(
        result["fees"]
        for result in results
    )

    total_slippage = sum(
        result["slippage"]
        for result in results
    )

    total_profit = 0.0
    total_loss = 0.0

    print("")
    print("================================")
    print("GESAMT-ZUSAMMENFASSUNG")
    print("================================")

    for result in results:
        print(
            result["symbol"],
            "| Trades:",
            result["trades"],
            "| Netto:",
            round(result["net_pnl"], 2),
            "USDT",
            "| PF:",
            round(result["profit_factor"], 2),
        )

    print("--------------------------------")
    print("Gesamt-Trades:", total_trades)
    print(
        "Gesamt-Netto:",
        round(total_net_pnl, 2),
        "USDT",
    )
    print(
        "Gesamt-Gebühren:",
        round(total_fees, 2),
        "USDT",
    )
    print(
        "Gesamt-Slippage:",
        round(total_slippage, 2),
        "USDT",
    )
    print("================================")
