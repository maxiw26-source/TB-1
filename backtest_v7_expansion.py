import csv
import os
from bisect import bisect_right

import config
from strategy_v7_bidir import calculate_signal

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
TP1_R = float(getattr(config, "TP1_R_MULTIPLE", 1.0))
TP2_R = float(getattr(config, "TP2_R_MULTIPLE", 1.2))
TP1_CLOSE_PERCENT = float(getattr(config, "TP1_CLOSE_PERCENT", 70.0))
SETUP_EXPIRY_CANDLES = int(getattr(config, "SETUP_EXPIRY_CANDLES", 5))
COOLDOWN_CANDLES = int(getattr(config, "COOLDOWN_CANDLES", 10))


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
    rows.sort(key=lambda c: c["timestamp"])
    return rows


def history_before(candles, times, timestamp):
    end = bisect_right(times, timestamp)
    return candles[max(0, end - CANDLE_LIMIT):end]


def maker_fee(price, qty):
    return price * qty * MAKER_FEE_PERCENT / 100.0


def taker_fee(price, qty):
    return price * qty * TAKER_FEE_PERCENT / 100.0


def slippage(price, qty):
    return price * qty * TAKER_SLIPPAGE_PERCENT / 100.0


def params_for(symbol):
    if symbol == "ETHUSDT":
        return 7, 12, 4, 0.05
    if symbol == "SOLUSDT":
        return 7, 10, 4, 0.06
    return 10, 8, 4, 0.10


def create_position(setup, balance, ts):
    side = setup["side"]
    entry = float(setup["entry"])
    stop = float(setup["stop"])
    risk_distance = entry - stop if side == "LONG" else stop - entry
    if risk_distance <= 0:
        return None

    risk_amount = balance * RISK_PERCENT / 100.0
    qty = min(
        risk_amount / risk_distance,
        (balance * MAX_MARGIN_PERCENT / 100.0 * LEVERAGE) / entry,
    )
    if qty <= 0:
        return None

    est_costs = maker_fee(entry, qty) + taker_fee(entry, qty) + slippage(entry, qty)
    actual_risk = risk_distance * qty
    ratio = est_costs / actual_risk if actual_risk > 0 else 999.0
    if ratio > MAX_COST_TO_RISK_RATIO:
        return None

    if side == "LONG":
        tp1 = entry + risk_distance * TP1_R
        tp2 = entry + risk_distance * TP2_R
    else:
        tp1 = entry - risk_distance * TP1_R
        tp2 = entry - risk_distance * TP2_R

    return {
        "side": side,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "qty": qty,
        "remaining": qty,
        "tp1_hit": False,
        "gross": 0.0,
        "fees": maker_fee(entry, qty),
        "slippage": 0.0,
        "entry_timestamp": int(ts),
        "reason": "",
    }


def close_part(position, price, qty, reason):
    qty = min(qty, position["remaining"])
    if qty <= 0:
        return
    if position["side"] == "LONG":
        position["gross"] += (price - position["entry"]) * qty
    else:
        position["gross"] += (position["entry"] - price) * qty

    if reason == "STOP":
        position["fees"] += taker_fee(price, qty)
        position["slippage"] += slippage(price, qty)
    else:
        position["fees"] += maker_fee(price, qty)

    position["remaining"] -= qty
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
                position["qty"] * TP1_CLOSE_PERCENT / 100.0,
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
                position["qty"] * TP1_CLOSE_PERCENT / 100.0,
                "TP1",
            )
            position["tp1_hit"] = True
            position["stop"] = position["entry"]
        if low <= position["tp2"] and position["remaining"] > 0:
            close_part(position, position["tp2"], position["remaining"], "TP2")
            return True

    return position["remaining"] <= 0


def finalize(position, ts):
    return {
        "side": position["side"],
        "entry_timestamp": position["entry_timestamp"],
        "exit_timestamp": int(ts),
        "net_pnl": position["gross"] - position["fees"] - position["slippage"],
        "reason": position["reason"],
        "tp1_hit": position["tp1_hit"],
    }


def run_symbol(symbol):
    entry = load_csv(symbol, "1m")
    conf = load_csv(symbol, "5m")
    trend = load_csv(symbol, "15m")

    cutoff = entry[-1]["timestamp"] - BACKTEST_DAYS * 24 * 60 * 60 * 1000
    entry = [c for c in entry if c["timestamp"] >= cutoff]
    conf = [c for c in conf if c["timestamp"] >= cutoff]
    trend = [c for c in trend if c["timestamp"] >= cutoff]

    conf_times = [c["timestamp"] for c in conf]
    trend_times = [c["timestamp"] for c in trend]

    swing, sweep_to_bos, bos_to_fvg, min_fvg = params_for(symbol)

    balance = START_BALANCE
    trades = []
    pending = None
    position = None
    used = set()
    cooldown_until = -1

    for i, candle in enumerate(entry):
        ts = candle["timestamp"]

        if position is not None:
            if check_position(position, candle):
                trade = finalize(position, ts)
                balance += trade["net_pnl"]
                trades.append(trade)
                position = None
                cooldown_until = i + COOLDOWN_CANDLES
            continue

        if pending is not None:
            setup = pending["setup"]
            age = i - pending["created_index"]

            invalid = (
                float(candle["low"]) <= float(setup["stop"])
                if setup["side"] == "LONG"
                else float(candle["high"]) >= float(setup["stop"])
            )

            if invalid or age > SETUP_EXPIRY_CANDLES:
                pending = None
            else:
                entry_price = float(setup["entry"])
                if float(candle["low"]) <= entry_price <= float(candle["high"]):
                    position = create_position(setup, balance, ts)
                    pending = None
                    continue

        if i <= cooldown_until or pending is not None:
            continue

        entry_hist = entry[max(0, i - CANDLE_LIMIT + 1): i + 1]
        conf_hist = history_before(conf, conf_times, ts)
        trend_hist = history_before(trend, trend_times, ts)

        result = calculate_signal(
            entry_hist,
            conf_hist,
            trend_hist,
            swing,
            sweep_to_bos,
            bos_to_fvg,
            min_fvg,
        )

        if result.get("signal") not in ("PENDING_LONG", "PENDING_SHORT"):
            continue

        setup_id = result["indicators"].get("setup_id")
        setup = result["indicators"].get("setup")

        if not setup_id or not setup or setup_id in used:
            continue

        used.add(setup_id)
        pending = {
            "setup": setup,
            "created_index": i,
        }

    wins = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    gp = sum(t["net_pnl"] for t in wins)
    gl = abs(sum(t["net_pnl"] for t in losses))
    pf = gp / gl if gl > 0 else float("inf")
    longs = [t for t in trades if t["side"] == "LONG"]
    shorts = [t for t in trades if t["side"] == "SHORT"]

    print("")
    print(symbol)
    print("Trades:", len(trades))
    print("LONG:", len(longs), "| SHORT:", len(shorts))
    print("Gewinner:", len(wins), "| Verlierer:", len(losses))
    print("Trefferquote:", f"{(100*len(wins)/len(trades) if trades else 0):.1f}%")
    print("Profit-Faktor:", "inf" if pf == float("inf") else f"{pf:.2f}")
    print("Netto PnL:", f"{balance - START_BALANCE:.2f} USDT")

    return {
        "symbol": symbol,
        "trades": len(trades),
        "net_pnl": balance - START_BALANCE,
        "profit_factor": pf,
        "longs": len(longs),
        "shorts": len(shorts),
    }


def main():
    print("V7 EXPANSION PAPER-BACKTEST")
    print("BTC + ETH + SOL | LONG + SHORT")
    print("Nur Simulation – keine Orders.")

    results = []
    for symbol in SYMBOLS:
        results.append(run_symbol(symbol))

    print("")
    print("GESAMT")
    print("Trades:", sum(r["trades"] for r in results))
    print("Netto PnL:", f"{sum(r['net_pnl'] for r in results):.2f} USDT")


if __name__ == "__main__":
    main()
