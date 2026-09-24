"""Research only: dollar-neutral ETH/BTC log-ratio reversal, no order API.

Signals from CLOSED synchronized 1h bars, enter next hour open on both legs.
Worst-case paired OHLC stops, fee/slippage on four fills, adverse funding.
No cointegration claim; an equal-dollar hedge retains material basis risk.
"""
import argparse
import csv
from math import log, sqrt
from pathlib import Path

from backtest_v8_walkforward import load_csv
from backtest_v9 import SLIPPAGE_PCT, TAKER_FEE_PCT, timestamp_text
from backtest_v12_hourly_trend import DAY_MS, HOUR_MS, aggregate_hourly

LOOKBACK = 240  # hours, frozen before evaluation
ENTRY_Z = 2.5
EXIT_Z = 0.5
STOP_Z = 4.0
MAX_HOLD = 48
LEG_USDT = 100.0
FUNDING_PER_8H_PCT = 0.01  # charged on each leg regardless of actual rate
FEE = TAKER_FEE_PCT / 100
SLIP = SLIPPAGE_PCT / 100


def paired_hours(btc, eth):
    by_ts = {c["timestamp"]: c for c in eth}
    return [(b["timestamp"], b, by_ts[b["timestamp"]]) for b in btc
            if b["timestamp"] in by_ts]


def ratio(eth_price, btc_price):
    if eth_price <= 0 or btc_price <= 0:
        raise ValueError("Nonpositive price")
    return log(eth_price / btc_price)


def signals(pairs):
    """All inputs for signal i end at the close of bar i."""
    out = [None] * len(pairs)
    streak = 1
    for i in range(1, len(pairs)):
        streak = streak + 1 if pairs[i][0] - pairs[i - 1][0] == HOUR_MS else 1
        if streak < LOOKBACK:
            continue
        window = [ratio(e["close"], b["close"]) for _, b, e in pairs[i - LOOKBACK + 1:i + 1]]
        mean = sum(window) / LOOKBACK
        sd = sqrt(sum((x - mean) ** 2 for x in window) / LOOKBACK)
        if sd < 0.0005:
            continue
        z = (window[-1] - mean) / sd
        if z >= ENTRY_Z:
            out[i] = (-1, mean, sd)  # short ETH, long BTC
        elif z <= -ENTRY_Z:
            out[i] = (+1, mean, sd)  # long ETH, short BTC
    return out


def open_pair(setup, btc, eth, ts):
    direction, mean, sd = setup
    # Market entry adverse to direction for each leg.
    e_side, b_side = direction, -direction
    e_entry = eth["open"] * (1 + e_side * SLIP)
    b_entry = btc["open"] * (1 + b_side * SLIP)
    z = (ratio(e_entry, b_entry) - mean) / sd
    if direction * z >= -ENTRY_Z:  # crossing vanished before next open
        return None
    return {"direction": direction, "mean": mean, "sd": sd, "eth": e_entry,
            "btc": b_entry, "eqty": LEG_USDT / e_entry, "bqty": LEG_USDT / b_entry,
            "entry_ts": ts, "entry_fee": 2 * LEG_USDT * FEE, "bars": 0}


def close_pair(pos, eth_raw, btc_raw, ts, reason):
    direction = pos["direction"]
    # Market exit: sell the long leg / buy the short leg; fee and slippage each.
    e_exit = eth_raw * (1 - direction * SLIP)
    b_exit = btc_raw * (1 + direction * SLIP)
    gross = direction * (e_exit - pos["eth"]) * pos["eqty"]
    gross -= direction * (b_exit - pos["btc"]) * pos["bqty"]
    exit_fee = (e_exit * pos["eqty"] + b_exit * pos["bqty"]) * FEE
    first = (pos["entry_ts"] // (8 * HOUR_MS) + 1) * (8 * HOUR_MS)
    periods = max(0, ((ts + HOUR_MS) - first) // (8 * HOUR_MS) + 1)
    funding = periods * 2 * LEG_USDT * FUNDING_PER_8H_PCT / 100
    return {"side": "LONG_ETH" if direction == 1 else "SHORT_ETH",
            "entry_ts": pos["entry_ts"], "exit_ts": ts, "entry_eth": pos["eth"],
            "entry_btc": pos["btc"], "exit_eth": e_exit, "exit_btc": b_exit,
            "gross": gross, "entry_fee": pos["entry_fee"], "exit_fee": exit_fee,
            "funding_cost": funding, "net": gross - pos["entry_fee"] - exit_fee - funding,
            "reason": reason}


def exit_prices(pos, btc, eth):
    """Apply simultaneous worst extremes for stops; targets use close only."""
    d, mean, sd = pos["direction"], pos["mean"], pos["sd"]
    e_worst = eth["low"] if d == 1 else eth["high"]
    b_worst = btc["high"] if d == 1 else btc["low"]
    worst_z = (ratio(e_worst, b_worst) - mean) / sd
    if d * worst_z <= -STOP_Z:
        # If the hour gaps across the stop, the open is worse: use that open.
        open_z = (ratio(eth["open"], btc["open"]) - mean) / sd
        if d * open_z <= -STOP_Z:
            return eth["open"], btc["open"], "GAP_STOP"
        return e_worst, b_worst, "STOP"
    close_z = (ratio(eth["close"], btc["close"]) - mean) / sd
    if d * close_z >= -EXIT_Z:
        return eth["close"], btc["close"], "MEAN"
    if pos["bars"] >= MAX_HOLD:
        return eth["close"], btc["close"], "TIME"
    return None


def run_window(pairs, setups, start, end):
    trades = []
    pos = pending = None
    balance = peak = 10000.0
    dd = 0.0
    last = None

    def settle(e_price, b_price, ts, reason):
        nonlocal pos, balance, peak, dd
        trade = close_pair(pos, e_price, b_price, ts, reason)
        trades.append(trade)
        balance += trade["net"]
        peak = max(peak, balance)
        dd = max(dd, (peak - balance) * 100 / peak)
        pos = None

    for i, (ts, btc, eth) in enumerate(pairs):
        if not start <= ts <= end:
            continue
        last = ts, btc, eth
        gap = i == 0 or ts - pairs[i - 1][0] != HOUR_MS
        if gap:
            pending = None
            if pos is not None:
                settle(eth["open"], btc["open"], ts, "DATA_GAP")
        if pos is not None:
            pos["bars"] += 1
            hit = exit_prices(pos, btc, eth)
            if hit:
                settle(hit[0], hit[1], ts, hit[2])
        if pos is None and pending is not None and not gap:
            pos = open_pair(pending, btc, eth, ts)
            if pos is not None:
                pos["bars"] = 1
                hit = exit_prices(pos, btc, eth)
                if hit:
                    settle(hit[0], hit[1], ts, hit[2])
        pending = setups[i] if pos is None and ts + HOUR_MS <= end else None
    if pos is not None:
        _, btc, eth = last
        settle(eth["close"], btc["close"], last[0], "WINDOW_END")
    gains = sum(t["net"] for t in trades if t["net"] > 0)
    losses = -sum(t["net"] for t in trades if t["net"] <= 0)
    return {"trades": trades, "count": len(trades),
            "net": balance - 10000, "pf": gains / losses if losses else (float("inf") if gains else 0),
            "win_pct": sum(t["net"] > 0 for t in trades) * 100 / len(trades) if trades else 0,
            "fees": sum(t["entry_fee"] + t["exit_fee"] for t in trades),
            "funding": sum(t["funding_cost"] for t in trades), "dd": dd}


def main():
    ap = argparse.ArgumentParser(description="V14 ETH/BTC pair backtest only")
    ap.add_argument("--train-days", type=int, default=180)
    ap.add_argument("--test-days", type=int, default=90)
    args = ap.parse_args()
    if args.train_days < 30 or args.test_days < 30:
        ap.error("Each window must contain at least 30 days")
    print("V14 ETH/BTC PAIR – NUR HISTORISCH, KEINE ORDERS", flush=True)
    print("240h ETH/BTC-Log-Ratio, Einstieg 2.5 Sigma, Ziel 0.5, Stop 4, max 48h; "
          "100 USDT je Leg; Taker/Slippage 4 Fills; adverse Funding auf beide Legs.", flush=True)
    print("Kein Cointegrationsnachweis; Ratio kann dauerhaft driften. TEST schon mehrfach angesehen.", flush=True)
    try:
        btc_five = load_csv("BTCUSDT", "5m")
        eth_five = load_csv("ETHUSDT", "5m")
    except FileNotFoundError as exc:
        ap.error(f"Missing data: {exc}")
    for symbol, data in (("BTC", btc_five), ("ETH", eth_five)):
        if len(data) < 3000 or any(b["timestamp"] <= a["timestamp"] for a, b in zip(data, data[1:])):
            ap.error(f"Invalid or duplicate {symbol} data")
    pairs = paired_hours(aggregate_hourly(btc_five), aggregate_hourly(eth_five))
    if not pairs:
        ap.error("No synchronized hours")
    end = pairs[-1][0] - HOUR_MS
    split = end - args.test_days * DAY_MS
    start = split - args.train_days * DAY_MS
    if pairs[0][0] > start - (LOOKBACK - 1) * HOUR_MS:
        ap.error("Insufficient common history for warmup")
    print("Gemeinsame 1h:", len(pairs), "von", timestamp_text(pairs[0][0]),
          "bis", timestamp_text(pairs[-1][0]), flush=True)
    setups = signals(pairs)
    fields = ("side", "entry_ts", "exit_ts", "entry_eth", "entry_btc", "exit_eth",
              "exit_btc", "gross", "entry_fee", "exit_fee", "funding_cost", "net", "reason")
    for label, lo, hi, days in (("TRAIN", start, split - HOUR_MS, args.train_days),
                                ("TEST", split, end, args.test_days)):
        count = sum(lo <= ts <= hi for ts, _, _ in pairs)
        print(f"{label} gemeinsame Stunden: {count}/{days * 24}", flush=True)
        if count < days * 24 * 0.90:
            print(f"{label} Daten unvollstaendig; kein Ergebnis.", flush=True)
            continue
        r = run_window(pairs, setups, lo, hi)
        print(f"{label} | Trades {r['count']} | Treffer {r['win_pct']:.1f}% | "
              f"PF {r['pf']:.2f} | Netto {r['net']:+.2f} USDT | "
              f"Gebuehren {r['fees']:.2f} | Funding-Annahme {r['funding']:.2f} | "
              f"Realisiert-DD {r['dd']:.2f}%", flush=True)
        output = Path("v14_results") / f"eth_btc_{label.lower()}.csv"
        output.parent.mkdir(exist_ok=True)
        with output.open("w", encoding="utf-8", newline="") as file:
            writer = csv.DictWriter(file, fieldnames=fields)
            writer.writeheader()
            writer.writerows(r["trades"])
    print("FERTIG – CSVs in v14_results; keine Live-Aenderung.", flush=True)


if __name__ == "__main__":
    main()
