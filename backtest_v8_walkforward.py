import csv
import os
from bisect import bisect_right

import config
from strategy_v8 import calculate_signal

DATA_FOLDER = "historical_data"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]

TRAIN_DAYS = 245
TEST_DAYS = 120
CANDLE_LIMIT = max(int(getattr(config, "CANDLE_LIMIT", 300)), 220)

START_BALANCE = float(getattr(config, "START_BALANCE", 10000.0))
RISK_PERCENT = float(getattr(config, "RISK_PER_TRADE_PERCENT", 0.25))
LEVERAGE = float(getattr(config, "LEVERAGE", 3))
MAX_MARGIN_PERCENT = float(getattr(config, "MAX_MARGIN_PERCENT", 20.0))

MAKER_FEE_PERCENT = float(getattr(config, "MAKER_FEE_PERCENT", 0.02))
TAKER_FEE_PERCENT = float(getattr(config, "TAKER_FEE_PERCENT", 0.06))
TAKER_SLIPPAGE_PERCENT = float(getattr(config, "TAKER_SLIPPAGE_PERCENT", 0.02))
MAX_COST_TO_RISK_RATIO = float(getattr(config, "MAX_COST_TO_RISK_RATIO", 0.12))

TP1_CLOSE_PERCENT = 70.0

PROFILES = [
    {
        "name": "baseline",
        "swing_lookback": 10,
        "bos_lookback": 10,
        "sweep_to_bos_candles": 8,
        "bos_to_fvg_candles": 4,
        "setup_expiry_candles": 5,
        "min_fvg_atr_ratio": 0.10,
        "sweep_buffer_atr": 0.05,
        "stop_buffer_atr": 0.10,
        "adx_minimum": 20.0,
        "tp1_r": 1.0,
        "tp2_r": 1.2,
    },
    {
        "name": "balanced",
        "swing_lookback": 8,
        "bos_lookback": 8,
        "sweep_to_bos_candles": 10,
        "bos_to_fvg_candles": 5,
        "setup_expiry_candles": 6,
        "min_fvg_atr_ratio": 0.07,
        "sweep_buffer_atr": 0.04,
        "stop_buffer_atr": 0.10,
        "adx_minimum": 18.0,
        "tp1_r": 1.0,
        "tp2_r": 1.3,
    },
    {
        "name": "frequent",
        "swing_lookback": 6,
        "bos_lookback": 7,
        "sweep_to_bos_candles": 12,
        "bos_to_fvg_candles": 6,
        "setup_expiry_candles": 8,
        "min_fvg_atr_ratio": 0.04,
        "sweep_buffer_atr": 0.03,
        "stop_buffer_atr": 0.12,
        "adx_minimum": 16.0,
        "tp1_r": 0.9,
        "tp2_r": 1.25,
    },
]


def load_csv(symbol, interval):
    path = os.path.join(DATA_FOLDER, f"{symbol}_{interval}.csv")

    if not os.path.exists(path):
        raise FileNotFoundError(path)

    rows = []

    with open(path, "r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append(
                {
                    "timestamp": int(row["timestamp"]),
                    "open": float(row["open"]),
                    "high": float(row["high"]),
                    "low": float(row["low"]),
                    "close": float(row["close"]),
                    "volume": float(row.get("volume", 0) or 0),
                }
            )

    rows.sort(key=lambda candle: candle["timestamp"])
    return rows


def history_before(candles, times, timestamp):
    end = bisect_right(times, timestamp)
    return candles[max(0, end - CANDLE_LIMIT):end]


def maker_fee(price, quantity):
    return price * quantity * MAKER_FEE_PERCENT / 100.0


def taker_fee(price, quantity):
    return price * quantity * TAKER_FEE_PERCENT / 100.0


def slippage(price, quantity):
    return price * quantity * TAKER_SLIPPAGE_PERCENT / 100.0


def create_position(setup, balance, profile, timestamp):
    side = setup["side"]
    entry = float(setup["entry"])
    stop = float(setup["stop"])

    risk_distance = (
        entry - stop
        if side == "LONG"
        else stop - entry
    )

    if risk_distance <= 0:
        return None

    risk_amount = balance * RISK_PERCENT / 100.0

    quantity = min(
        risk_amount / risk_distance,
        (
            balance
            * MAX_MARGIN_PERCENT
            / 100.0
            * LEVERAGE
        )
        / entry,
    )

    if quantity <= 0:
        return None

    estimated_costs = (
        maker_fee(entry, quantity)
        + taker_fee(entry, quantity)
        + slippage(entry, quantity)
    )

    actual_risk = risk_distance * quantity

    if actual_risk <= 0:
        return None

    cost_ratio = estimated_costs / actual_risk

    if cost_ratio > MAX_COST_TO_RISK_RATIO:
        return None

    if side == "LONG":
        tp1 = entry + risk_distance * float(profile["tp1_r"])
        tp2 = entry + risk_distance * float(profile["tp2_r"])
    else:
        tp1 = entry - risk_distance * float(profile["tp1_r"])
        tp2 = entry - risk_distance * float(profile["tp2_r"])

    return {
        "side": side,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "quantity": quantity,
        "remaining": quantity,
        "tp1_hit": False,
        "gross": 0.0,
        "fees": maker_fee(entry, quantity),
        "slippage": 0.0,
        "entry_timestamp": int(timestamp),
        "reason": "",
    }


def close_part(position, price, quantity, reason):
    quantity = min(quantity, position["remaining"])

    if quantity <= 0:
        return

    if position["side"] == "LONG":
        position["gross"] += (
            price - position["entry"]
        ) * quantity
    else:
        position["gross"] += (
            position["entry"] - price
        ) * quantity

    if reason == "STOP":
        position["fees"] += taker_fee(price, quantity)
        position["slippage"] += slippage(price, quantity)
    else:
        position["fees"] += maker_fee(price, quantity)

    position["remaining"] -= quantity

    if position["remaining"] < 1e-12:
        position["remaining"] = 0.0

    position["reason"] = reason


def check_position(position, candle):
    high = float(candle["high"])
    low = float(candle["low"])

    if position["side"] == "LONG":
        if low <= position["stop"]:
            close_part(position, position["stop"], position["remaining"], "STOP")
            return True

        if not position["tp1_hit"] and high >= position["tp1"]:
            close_part(
                position,
                position["tp1"],
                position["quantity"] * TP1_CLOSE_PERCENT / 100.0,
                "TP1",
            )
            position["tp1_hit"] = True
            position["stop"] = position["entry"]

        if high >= position["tp2"] and position["remaining"] > 0:
            close_part(position, position["tp2"], position["remaining"], "TP2")
            return True

    else:
        if high >= position["stop"]:
            close_part(position, position["stop"], position["remaining"], "STOP")
            return True

        if not position["tp1_hit"] and low <= position["tp1"]:
            close_part(
                position,
                position["tp1"],
                position["quantity"] * TP1_CLOSE_PERCENT / 100.0,
                "TP1",
            )
            position["tp1_hit"] = True
            position["stop"] = position["entry"]

        if low <= position["tp2"] and position["remaining"] > 0:
            close_part(position, position["tp2"], position["remaining"], "TP2")
            return True

    return position["remaining"] <= 0


def finalize(position, timestamp):
    return {
        "side": position["side"],
        "entry_timestamp": position["entry_timestamp"],
        "exit_timestamp": int(timestamp),
        "net_pnl": (
            position["gross"]
            - position["fees"]
            - position["slippage"]
        ),
    }


def stats(trades):
    winners = [
        trade
        for trade in trades
        if trade["net_pnl"] > 0
    ]

    losers = [
        trade
        for trade in trades
        if trade["net_pnl"] <= 0
    ]

    gross_profit = sum(
        trade["net_pnl"]
        for trade in winners
    )

    gross_loss = abs(
        sum(
            trade["net_pnl"]
            for trade in losers
        )
    )

    if gross_loss > 0:
        profit_factor = gross_profit / gross_loss
    elif gross_profit > 0:
        profit_factor = float("inf")
    else:
        profit_factor = 0.0

    longs = [
        trade
        for trade in trades
        if trade["side"] == "LONG"
    ]

    shorts = [
        trade
        for trade in trades
        if trade["side"] == "SHORT"
    ]

    return {
        "trades": len(trades),
        "longs": len(longs),
        "shorts": len(shorts),
        "net": sum(
            trade["net_pnl"]
            for trade in trades
        ),
        "pf": profit_factor,
        "win_rate": (
            100.0
            * len(winners)
            / len(trades)
            if trades
            else 0.0
        ),
    }


def run_window(symbol, profile, start_ts, end_ts):
    entry_all = load_csv(symbol, "1m")
    confirmation_all = load_csv(symbol, "5m")
    trend_all = load_csv(symbol, "15m")

    warmup = 4 * 24 * 60 * 60 * 1000

    entry = [
        candle
        for candle in entry_all
        if (
            start_ts - warmup
            <= candle["timestamp"]
            <= end_ts
        )
    ]

    confirmation = [
        candle
        for candle in confirmation_all
        if (
            start_ts - warmup
            <= candle["timestamp"]
            <= end_ts
        )
    ]

    trend = [
        candle
        for candle in trend_all
        if (
            start_ts - warmup
            <= candle["timestamp"]
            <= end_ts
        )
    ]

    confirmation_times = [
        candle["timestamp"]
        for candle in confirmation
    ]

    trend_times = [
        candle["timestamp"]
        for candle in trend
    ]

    balance = START_BALANCE
    trades = []

    pending = None
    position = None
    used_setups = set()
    cooldown_until = -1

    for index, candle in enumerate(entry):
        timestamp = candle["timestamp"]

        if timestamp < start_ts:
            continue

        if position is not None:
            if check_position(position, candle):
                trade = finalize(position, timestamp)
                trades.append(trade)
                balance += trade["net_pnl"]
                position = None
                cooldown_until = index + 10
            continue

        if pending is not None:
            setup = pending["setup"]
            age = index - pending["created_index"]

            invalid = (
                float(candle["low"]) <= float(setup["stop"])
                if setup["side"] == "LONG"
                else float(candle["high"]) >= float(setup["stop"])
            )

            if invalid or age > int(profile["setup_expiry_candles"]):
                pending = None
            else:
                entry_price = float(setup["entry"])

                if (
                    float(candle["low"])
                    <= entry_price
                    <= float(candle["high"])
                ):
                    position = create_position(
                        setup,
                        balance,
                        profile,
                        timestamp,
                    )
                    pending = None

                    if position is not None:
                        continue

        if pending is not None or index <= cooldown_until:
            continue

        entry_history = entry[
            max(0, index - CANDLE_LIMIT + 1):
            index + 1
        ]

        confirmation_history = history_before(
            confirmation,
            confirmation_times,
            timestamp,
        )

        trend_history = history_before(
            trend,
            trend_times,
            timestamp,
        )

        result = calculate_signal(
            entry_history,
            confirmation_history,
            trend_history,
            profile,
        )

        if result.get("signal") not in {
            "PENDING_LONG",
            "PENDING_SHORT",
        }:
            continue

        indicators = result.get("indicators") or {}
        setup_id = indicators.get("setup_id")
        setup = indicators.get("setup")

        if not setup_id or not setup or setup_id in used_setups:
            continue

        used_setups.add(setup_id)

        pending = {
            "setup": setup,
            "created_index": index,
        }

    if position is not None and entry:
        last_candle = entry[-1]
        last_price = float(last_candle["close"])

        close_part(
            position,
            last_price,
            position["remaining"],
            "WINDOW_END",
        )

        trades.append(
            finalize(
                position,
                last_candle["timestamp"],
            )
        )

    return stats(trades)


def score(result):
    if result["trades"] < 10:
        return -999999.0

    if result["pf"] <= 1.0:
        return -999999.0

    # Prefer robustness first, then activity.
    return (
        result["pf"] * 100.0
        + min(result["trades"], 100) * 0.5
        + result["net"] * 0.01
    )


def main():
    print("LSOB V8 WALK-FORWARD PAPER-TEST")
    print("BTC + ETH + SOL | LONG + SHORT")
    print("Training:", TRAIN_DAYS, "Tage | Test:", TEST_DAYS, "Tage")
    print("Keine Live-Änderung.")

    for symbol in SYMBOLS:
        one_minute = load_csv(symbol, "1m")

        end_ts = one_minute[-1]["timestamp"]
        test_start = end_ts - TEST_DAYS * 24 * 60 * 60 * 1000
        train_start = test_start - TRAIN_DAYS * 24 * 60 * 60 * 1000

        print("")
        print("================================")
        print(symbol)
        print("================================")

        train_results = []

        for profile in PROFILES:
            result = run_window(
                symbol,
                profile,
                train_start,
                test_start - 60_000,
            )

            train_results.append(
                (profile, result)
            )

            pf_text = (
                "inf"
                if result["pf"] == float("inf")
                else f"{result['pf']:.2f}"
            )

            print(
                "TRAIN",
                profile["name"],
                "| Trades:", result["trades"],
                "| L/S:", f"{result['longs']}/{result['shorts']}",
                "| PF:", pf_text,
                "| Netto:", f"{result['net']:.2f}",
            )

        ranked = sorted(
            train_results,
            key=lambda item: score(item[1]),
            reverse=True,
        )

        best_profile, best_train = ranked[0]

        if score(best_train) < 0:
            print(
                "Kein Trainingsprofil erfüllt "
                "die Mindestkriterien "
                "(>=10 Trades und PF > 1)."
            )
            continue

        test_result = run_window(
            symbol,
            best_profile,
            test_start,
            end_ts,
        )

        test_pf = (
            "inf"
            if test_result["pf"] == float("inf")
            else f"{test_result['pf']:.2f}"
        )

        print(
            "GEWÄHLT:",
            best_profile["name"],
            "| TRAIN Trades:", best_train["trades"],
            "| TRAIN PF:",
            (
                "inf"
                if best_train["pf"] == float("inf")
                else f"{best_train['pf']:.2f}"
            ),
        )

        print(
            "OUT-OF-SAMPLE",
            "| Trades:", test_result["trades"],
            "| L/S:", f"{test_result['longs']}/{test_result['shorts']}",
            "| Trefferquote:", f"{test_result['win_rate']:.1f}%",
            "| PF:", test_pf,
            "| Netto:", f"{test_result['net']:.2f} USDT",
        )


if __name__ == "__main__":
    main()
