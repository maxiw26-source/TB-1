import csv
import os
from bisect import bisect_right

import config
from strategy_breakout_v2 import atr
from strategy_breakout_v3 import calculate_candidate, retest_plan

DATA_FOLDER = "historical_data"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT"]
CANDLE_LIMIT = max(int(getattr(config, "CANDLE_LIMIT", 300)), 220)
START_BALANCE = float(getattr(config, "START_BALANCE", 10000.0))
RISK_PERCENT = float(getattr(config, "RISK_PER_TRADE_PERCENT", 0.25))
LEVERAGE = float(getattr(config, "LEVERAGE", 3))
MAX_MARGIN_PERCENT = float(getattr(config, "MAX_MARGIN_PERCENT", 20.0))
MAKER_FEE_PERCENT = float(getattr(config, "MAKER_FEE_PERCENT", 0.02))
TAKER_FEE_PERCENT = float(getattr(config, "TAKER_FEE_PERCENT", 0.06))
TAKER_SLIPPAGE_PERCENT = float(getattr(config, "TAKER_SLIPPAGE_PERCENT", 0.02))
MAX_COST_TO_RISK_RATIO = float(getattr(config, "MAX_COST_TO_RISK_RATIO", 0.12))

TRAIN_DAYS = 245
TEST_DAYS = 120

CANDIDATES = [
    {
        "name": "balanced",
        "lookback": 20,
        "breakout_buffer_percent": 0.05,
        "retest_tolerance_percent": 0.12,
        "retest_confirm_percent": 0.03,
        "retest_expiry_candles": 10,
        "adx_minimum": 18,
        "stop_buffer_atr": 0.10,
        "tp1_r": 0.8,
        "tp2_r": 1.5,
    },
    {
        "name": "selective",
        "lookback": 30,
        "breakout_buffer_percent": 0.08,
        "retest_tolerance_percent": 0.10,
        "retest_confirm_percent": 0.04,
        "retest_expiry_candles": 8,
        "adx_minimum": 22,
        "stop_buffer_atr": 0.12,
        "tp1_r": 1.0,
        "tp2_r": 1.8,
    },
    {
        "name": "frequent",
        "lookback": 15,
        "breakout_buffer_percent": 0.03,
        "retest_tolerance_percent": 0.15,
        "retest_confirm_percent": 0.02,
        "retest_expiry_candles": 12,
        "adx_minimum": 16,
        "stop_buffer_atr": 0.10,
        "tp1_r": 0.8,
        "tp2_r": 1.4,
    },
]


def load_csv(symbol, interval):
    path = os.path.join(DATA_FOLDER, f"{symbol}_{interval}.csv")
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    rows = []
    with open(path, "r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            rows.append({
                "timestamp": int(row["timestamp"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row.get("volume", 0) or 0),
            })
    rows.sort(key=lambda x: x["timestamp"])
    return rows


def history_before(candles, times, timestamp):
    end = bisect_right(times, timestamp)
    return candles[max(0, end - CANDLE_LIMIT):end]


def maker_fee(price, qty):
    return price * qty * MAKER_FEE_PERCENT / 100.0


def taker_fee(price, qty):
    return price * qty * TAKER_FEE_PERCENT / 100.0


def taker_slippage(price, qty):
    return price * qty * TAKER_SLIPPAGE_PERCENT / 100.0


def create_position(plan, balance):
    entry = float(plan["entry"])
    stop = float(plan["stop"])
    risk_distance = abs(entry - stop)
    if risk_distance <= 0:
        return None

    risk_qty = (balance * RISK_PERCENT / 100.0) / risk_distance
    max_notional = balance * MAX_MARGIN_PERCENT / 100.0 * LEVERAGE
    qty = min(risk_qty, max_notional / entry)
    if qty <= 0:
        return None

    est_cost = maker_fee(entry, qty) + taker_fee(entry, qty) + taker_slippage(entry, qty)
    actual_risk = risk_distance * qty
    cost_ratio = est_cost / actual_risk if actual_risk > 0 else 999.0
    if cost_ratio > MAX_COST_TO_RISK_RATIO:
        return None

    return {
        **plan,
        "quantity": qty,
        "remaining": qty,
        "tp1_hit": False,
        "gross_pnl": 0.0,
        "fees": maker_fee(entry, qty),
        "slippage": 0.0,
        "last_exit": entry,
        "reason": "",
    }


def exit_part(position, price, qty, reason):
    qty = min(qty, position["remaining"])
    if qty <= 0:
        return

    if position["side"] == "LONG":
        position["gross_pnl"] += (price - position["entry"]) * qty
    else:
        position["gross_pnl"] += (position["entry"] - price) * qty

    if reason == "STOP":
        position["fees"] += taker_fee(price, qty)
        position["slippage"] += taker_slippage(price, qty)
    else:
        position["fees"] += maker_fee(price, qty)

    position["remaining"] -= qty
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

    return position["remaining"] <= 1e-12


def stats(trades):
    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    gp = sum(t["net_pnl"] for t in wins)
    gl = abs(sum(t["net_pnl"] for t in losses))
    pf = gp / gl if gl > 0 else (float("inf") if gp > 0 else 0.0)
    return {
        "trades": len(trades),
        "net": sum(t["net_pnl"] for t in trades),
        "pf": pf,
        "win_rate": 100.0 * len(wins) / len(trades) if trades else 0.0,
    }


def run_window(symbol, params, start_ts, end_ts):
    entry_all = load_csv(symbol, "1m")
    conf_all = load_csv(symbol, "5m")
    trend_all = load_csv(symbol, "15m")

    warmup = 4 * 24 * 60 * 60 * 1000
    entry = [c for c in entry_all if start_ts - warmup <= c["timestamp"] <= end_ts]
    conf = [c for c in conf_all if start_ts - warmup <= c["timestamp"] <= end_ts]
    trend = [c for c in trend_all if start_ts - warmup <= c["timestamp"] <= end_ts]

    conf_times = [c["timestamp"] for c in conf]
    trend_times = [c["timestamp"] for c in trend]

    balance = START_BALANCE
    trades = []
    position = None
    pending = None
    cooldown_until = -1

    for index, candle in enumerate(entry):
        ts = candle["timestamp"]
        if ts < start_ts:
            continue

        if position is not None:
            if check_position(position, candle):
                net = position["gross_pnl"] - position["fees"] - position["slippage"]
                trades.append({"net_pnl": net})
                balance += net
                position = None
                cooldown_until = index + 5
            continue

        if index <= cooldown_until:
            continue

        entry_history = entry[max(0, index - CANDLE_LIMIT + 1): index + 1]
        conf_history = history_before(conf, conf_times, ts)
        trend_history = history_before(trend, trend_times, ts)

        current_atr = atr(entry_history, int(getattr(config, "ATR_PERIOD", 14)))

        if pending is not None:
            age = index - pending["created_index"]
            if age > int(params["retest_expiry_candles"]):
                pending = None
            elif current_atr is not None and age >= 1:
                plan = retest_plan(candle, pending, current_atr, params)
                if plan is not None:
                    position = create_position(plan, balance)
                    pending = None
                    if position is not None:
                        continue

        if pending is not None:
            continue

        breakout = calculate_candidate(entry_history, conf_history, trend_history, params)
        if breakout is None:
            continue

        pending = {
            **breakout,
            "created_index": index,
        }

    if position is not None:
        last_price = float(entry[-1]["close"])
        exit_part(position, last_price, position["remaining"], "WINDOW_END")
        net = position["gross_pnl"] - position["fees"] - position["slippage"]
        trades.append({"net_pnl": net})

    return stats(trades)


def main():
    print("BREAKOUT V3 WALK-FORWARD PAPER-TEST")
    print("Training:", TRAIN_DAYS, "Tage | Test:", TEST_DAYS, "Tage")
    print("Keine Orders. V7-Live bleibt unverändert.")

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
        for params in CANDIDATES:
            result = run_window(symbol, params, train_start, test_start - 60_000)
            train_results.append((params, result))
            pf_text = "inf" if result["pf"] == float("inf") else f"{result['pf']:.2f}"
            print(
                "TRAIN",
                params["name"],
                "| Trades:", result["trades"],
                "| PF:", pf_text,
                "| Netto:", f"{result['net']:.2f}",
            )

        viable = [
            item for item in train_results
            if item[1]["trades"] >= 8 and item[1]["pf"] > 1.0
        ]

        if not viable:
            print("Kein Trainings-Kandidat erfüllt Mindestkriterien.")
            continue

        best_params, best_train = max(
            viable,
            key=lambda item: (item[1]["pf"], item[1]["net"]),
        )

        test_result = run_window(symbol, best_params, test_start, end_ts)
        pf_text = "inf" if test_result["pf"] == float("inf") else f"{test_result['pf']:.2f}"

        print(
            "GEWÄHLT:",
            best_params["name"],
            "| TRAIN PF:",
            "inf" if best_train["pf"] == float("inf") else f"{best_train['pf']:.2f}",
        )
        print(
            "OUT-OF-SAMPLE TEST",
            "| Trades:", test_result["trades"],
            "| Trefferquote:", f"{test_result['win_rate']:.1f}%",
            "| PF:", pf_text,
            "| Netto:", f"{test_result['net']:.2f} USDT",
        )


if __name__ == "__main__":
    main()
