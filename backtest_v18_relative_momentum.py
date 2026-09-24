"""V18 research: rotate long exposure among BTC, ETH, ADA or remain in cash.

Decision: completed 23:00 UTC hourly candle, next 00:00 UTC open execution.
BTC 30-day absolute trend gate; strongest positive 14-day return is candidate.
A held asset is retained until a replacement leads by 3 percentage points.
Fees, slippage, adverse assumed funding are charged. No order API.
"""
import argparse
import csv
from collections import Counter
from pathlib import Path

from backtest_v8_walkforward import load_csv
from backtest_v9 import SLIPPAGE_PCT, TAKER_FEE_PCT, timestamp_text
from backtest_v12_hourly_trend import DAY_MS, HOUR_MS, aggregate_hourly

SYMBOLS = ("BTCUSDT", "ETHUSDT", "ADAUSDT")
NOTIONAL_USDT = 100.0
BTC_GATE_DAYS = 30
RANK_DAYS = 14
SWITCH_MARGIN = 0.03
MIN_HISTORY_COVERAGE = .95
FUNDING_PCT = .01  # adverse assumption per 8h, not historical funding
FEE = TAKER_FEE_PCT / 100
SLIP = SLIPPAGE_PCT / 100
START_BALANCE = 10000.0


def load_hourly():
    hourly = {}
    for symbol in SYMBOLS:
        five = load_csv(symbol, "5m")
        if len(five) < 1000 or any(b["timestamp"] <= a["timestamp"]
                                   for a, b in zip(five, five[1:])):
            raise ValueError(f"Ungueltige oder doppelte 5m-Daten: {symbol}")
        hourly[symbol] = {int(b["timestamp"]): b for b in aggregate_hourly(five)}
        print(f"{symbol} 5m: {timestamp_text(five[0]['timestamp'])} bis "
              f"{timestamp_text(five[-1]['timestamp'])}", flush=True)
    return hourly


def window_coverage(data, end_ts, days):
    start = end_ts - days * DAY_MS
    count = sum(start <= ts <= end_ts for ts in data)
    return count / (days * 24 + 1)


def momentum(data, end_ts, days):
    old = data.get(end_ts - days * DAY_MS)
    latest = data.get(end_ts)
    if (old is None or latest is None or
            window_coverage(data, end_ts, days) < MIN_HISTORY_COVERAGE):
        return None
    return latest["close"] / old["close"] - 1


def choice(hourly, decision_ts, held=None):
    """Only data up to the preceding hourly close is ever considered."""
    last = decision_ts - HOUR_MS
    btc_trend = momentum(hourly["BTCUSDT"], last, BTC_GATE_DAYS)
    if btc_trend is None:
        return None, "NO_BTC_HISTORY"
    if btc_trend <= 0:
        return None, "BTC_RISK_OFF"
    ranks = {symbol: momentum(hourly[symbol], last, RANK_DAYS)
             for symbol in SYMBOLS}
    available = {s: value for s, value in ranks.items()
                 if value is not None and value > 0}
    if not available:
        return None, "NO_POSITIVE_MOMENTUM"
    best = max(available, key=available.get)
    if held in available and available[best] < available[held] + SWITCH_MARGIN:
        best = held
    return best, "SELECTED"


def funding(entry_ts, exit_ts, notional):
    interval = 8 * HOUR_MS
    first = (entry_ts // interval + 1) * interval
    periods = max(0, (exit_ts - first) // interval + 1)
    return periods * notional * FUNDING_PCT / 100


def run_window(hourly, start, end):
    """Separate window; position and PnL reset at the window boundary."""
    trades = []
    held = None
    peak = balance = START_BALANCE
    max_dd = 0.0
    reasons = Counter()
    data_issues = 0
    position_hours = 0

    def close(raw_price, ts, reason):
        nonlocal held, balance, peak, max_dd
        if held is None:
            return
        price = raw_price * (1 - SLIP)
        exit_fee = price * held["qty"] * FEE
        funding_fee = funding(held["entry_ts"], ts, NOTIONAL_USDT)
        net = (price - held["entry"]) * held["qty"] - held["entry_fee"] - exit_fee - funding_fee
        trades.append({"symbol": held["symbol"], "entry_ts": held["entry_ts"],
                       "exit_ts": ts, "entry": held["entry"], "exit": price,
                       "qty": held["qty"], "entry_fee": held["entry_fee"],
                       "exit_fee": exit_fee, "funding_assumed": funding_fee,
                       "net": net, "reason": reason})
        balance += net
        peak = max(peak, balance)
        max_dd = max(max_dd, (peak - balance) / peak * 100)
        held = None

    # Midnight after a completed 23:00 UTC candle. This day starts with an
    # unknown portfolio even if the TRAIN window ended in a position.
    for ts in range(start, end + DAY_MS, DAY_MS):
        if held is not None:
            position_hours += 24
        target, reason = choice(hourly, ts, held["symbol"] if held else None)
        reasons[reason] += 1
        if held and target != held["symbol"]:
            bar = hourly[held["symbol"]].get(ts)
            if bar is None:
                data_issues += 1
                # Without an observed exit fill, this window has no valid PnL.
                return {"invalid": f"Exit-00:00-Kerze fehlt: {held['symbol']} {timestamp_text(ts)}"}
            close(bar["open"], ts, reason)
        if target and held is None:
            bar = hourly[target].get(ts)
            if bar is None:
                data_issues += 1
                continue
            raw = bar["open"]
            entry = raw * (1 + SLIP)
            if entry <= 0:
                continue
            held = {"symbol": target, "entry": entry, "entry_ts": ts,
                    "qty": NOTIONAL_USDT / entry, "entry_fee": NOTIONAL_USDT * FEE}
    if data_issues:
        return {"invalid": f"{data_issues} fehlende 00:00-Entry-Kerzen im Fenster"}
    if held is not None:
        final_ts = end + DAY_MS - HOUR_MS
        bar = hourly[held["symbol"]].get(final_ts)
        if bar is None:
            return {"invalid": f"Schluss-Kerze fehlt: {held['symbol']} {timestamp_text(final_ts)}"}
        close(bar["close"], final_ts + HOUR_MS, "WINDOW_END")
    gains = sum(t["net"] for t in trades if t["net"] > 0)
    losses = -sum(t["net"] for t in trades if t["net"] <= 0)
    return {"trades": trades, "count": len(trades), "net": balance - START_BALANCE,
            "pf": gains / losses if losses else (float("inf") if gains else 0.0),
            "wins": sum(t["net"] > 0 for t in trades), "fees": sum(
                t["entry_fee"] + t["exit_fee"] for t in trades),
            "funding": sum(t["funding_assumed"] for t in trades),
            "realized_dd": max_dd, "reasons": reasons,
            "data_issues": data_issues, "position_hours": position_hours}


def btc_hold_baseline(hourly, start, end):
    """Same fixed notional and costs, buy BTC at start, sell at window end."""
    entry_bar = hourly["BTCUSDT"].get(start)
    exit_bar = hourly["BTCUSDT"].get(end + DAY_MS - HOUR_MS)
    if entry_bar is None or exit_bar is None:
        return None
    entry = entry_bar["open"] * (1 + SLIP)
    exit_price = exit_bar["close"] * (1 - SLIP)
    qty = NOTIONAL_USDT / entry
    return ((exit_price - entry) * qty - NOTIONAL_USDT * FEE -
            exit_price * qty * FEE - funding(start, end + DAY_MS, NOTIONAL_USDT))


def main():
    parser = argparse.ArgumentParser(description="V18 historical rotation; no orders")
    parser.add_argument("--train-days", type=int, default=180)
    parser.add_argument("--test-days", type=int, default=90)
    args = parser.parse_args()
    if args.train_days < 90 or args.test_days < 60:
        parser.error("TRAIN >=90 and TEST >=60 days")
    print("V18 RELATIVE MOMENTUM – NUR HISTORISCH, KEINE ORDERS", flush=True)
    print("BTC 30-Tage-Trend, bester positiver 14-Tage-Trend aus BTC/ETH/ADA; "
          "3 Prozentpunkte Vorsprung fuer Wechsel; 100 USDT Long/Cash, kein Hebel.", flush=True)
    print("1h-Kerzen nur bei 12/12 echten 5m; Folgetag-Open; Taker/Slippage "
          "beidseitig, Funding-Annahme 0.01% je 8h immer Kosten. "
          "Fruehere Strategieversuche haben die TEST-Zeit bereits betrachtet.", flush=True)
    try:
        hourly = load_hourly()
    except (FileNotFoundError, ValueError) as exc:
        print("Keine auswertbaren historischen Daten:", exc, flush=True)
        return
    # Last full UTC day present for each instrument: never use the live day.
    last_common = min(max(data) for data in hourly.values())
    end = (last_common // DAY_MS - 1) * DAY_MS
    split = end - (args.test_days - 1) * DAY_MS
    start = split - args.train_days * DAY_MS
    for label, lo, hi, n in (("TRAIN", start, split - DAY_MS, args.train_days),
                             ("TEST", split, end, args.test_days)):
        print(f"\n{label}: {timestamp_text(lo)} bis {timestamp_text(hi)} UTC", flush=True)
        coverage = {s: sum(lo <= ts < hi + DAY_MS for ts in data) / (n * 24)
                    for s, data in hourly.items()}
        print("1h-Abdeckung:", " | ".join(f"{s} {v:.1%}" for s, v in coverage.items()), flush=True)
        if any(v < .95 for v in coverage.values()) or min(hourly["BTCUSDT"]) > lo - (BTC_GATE_DAYS + 1) * DAY_MS:
            print("Unvollstaendige 1h-Daten oder zu wenig Warmup; kein Ergebnis.", flush=True)
            continue
        result = run_window(hourly, lo, hi)
        if result.get("invalid"):
            print("UNGUELTIG:", result["invalid"], flush=True)
            continue
        print(f"{label} | Trades {result['count']} | Treffer {result['wins']} | "
              f"PF {result['pf']:.2f} | Netto {result['net']:+.2f} USDT | "
              f"Gebuehren {result['fees']:.2f} | Funding-Annahme {result['funding']:.2f} | "
              f"Realisiert-DD {result['realized_dd']:.2f}%", flush=True)
        baseline = btc_hold_baseline(hourly, lo, hi)
        print(f"{label} BTC Buy-and-Hold (100 USDT, gleiche Kosten): "
              + (f"{baseline:+.2f} USDT" if baseline is not None else
                 "nicht berechenbar wegen fehlender Randkerze"), flush=True)
        print("Signaltage:", dict(result["reasons"]), "| "
              f"nicht gehandelte Entries ohne 00:00-Kerze: {result['data_issues']}", flush=True)
        if result["data_issues"]:
            print("Eingeschraenkt durch fehlende Ausfuehrungskerzen.", flush=True)
        folder = Path("v18_results")
        folder.mkdir(exist_ok=True)
        with (folder / f"rotation_{label.lower()}.csv").open("w", newline="", encoding="utf-8") as stream:
            fields = ("symbol", "entry_ts", "exit_ts", "entry", "exit", "qty",
                      "entry_fee", "exit_fee", "funding_assumed", "net", "reason")
            writer = csv.DictWriter(stream, fieldnames=fields)
            writer.writeheader()
            writer.writerows(result["trades"])
    print("FERTIG – nur historische Simulation, keine Live-Aenderung.", flush=True)


if __name__ == "__main__":
    main()
