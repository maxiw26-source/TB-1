"""Diagnose the V9 backtest CSVs without submitting any orders.

Read the existing fixed-profile TRAIN/TEST exports. Report exit reasons,
LONG/SHORT performance, fee burden, and net results for each market. This
is descriptive diagnosis on already-seen data, not new OOS evidence.
"""
import argparse
import csv
from collections import defaultdict
from pathlib import Path


FIELDS = ("gross", "entry_fee", "exit_fee", "net")


def read_trades(path):
    if not path.exists():
        return None
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        for field in FIELDS:
            row[field] = float(row[field])
        row["costs"] = row["entry_fee"] + row["exit_fee"]
    return rows


def summarize(trades):
    profit = sum(t["net"] for t in trades if t["net"] > 0)
    loss = -sum(t["net"] for t in trades if t["net"] <= 0)
    return {
        "trades": len(trades),
        "wins": sum(t["net"] > 0 for t in trades),
        "gross": sum(t["gross"] for t in trades),
        "fees": sum(t["costs"] for t in trades),
        "net": sum(t["net"] for t in trades),
        "pf": profit / loss if loss > 0 else (float("inf") if profit > 0 else 0.0),
    }


def show(label, trades):
    s = summarize(trades)
    rate = s["wins"] * 100 / s["trades"] if s["trades"] else 0.0
    print(
        f"{label:<17} Trades {s['trades']:>4} | Treffer {rate:5.1f}%"
        f" | Brutto {s['gross']:+10.2f}"
        f" | Gebuehren {s['fees']:9.2f}"
        f" | Netto {s['net']:+10.2f} | PF {s['pf']:.2f}",
        flush=True,
    )


def report(symbol, period, folder):
    path = folder / f"{symbol}_{period}.csv"
    trades = read_trades(path)
    if trades is None:
        print(f"{path}: nicht gefunden. Zuerst python backtest_v9.py ausfuehren.")
        return
    print(f"\n{symbol} {period.upper()} | {path}")
    show("GESAMT", trades)
    for side in ("LONG", "SHORT"):
        show(side, [t for t in trades if t["side"] == side])
    groups = defaultdict(list)
    for trade in trades:
        groups[trade["reason"]].append(trade)
    for reason in sorted(groups):
        show("EXIT " + reason, groups[reason])
    if trades:
        print("GROESSTE VERLUSTE:")
        for trade in sorted(trades, key=lambda x: x["net"])[:3]:
            print(
                f"  {trade['side']:5} | Grund {trade['reason']:10}"
                f" | Brutto {trade['gross']:+.2f}"
                f" | Gebuehren {trade['costs']:.2f}"
                f" | Netto {trade['net']:+.2f}"
            )


def main():
    parser = argparse.ArgumentParser(description="V9 Ergebnis-Diagnose ohne Orders")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "ADAUSDT"])
    parser.add_argument("--folder", type=Path, default=Path("v9_results"))
    args = parser.parse_args()
    print("V9 VERLUST-DIAGNOSE – nur vorhandene CSVs, keine echten Orders.")
    print("Brutto enthaelt bereits modellierte Slippage; Gebuehren separat.")
    for symbol in args.symbols:
        for period in ("train", "test"):
            report(symbol.upper(), period, args.folder)
    print("\nHinweis: Diagnostik auf bekannten Daten, kein neuer OOS-Test.")


if __name__ == "__main__":
    main()
