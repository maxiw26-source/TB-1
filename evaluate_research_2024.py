"""Blind 2024 evaluation of previously frozen V12/V13/V15/V16/V17 rules.

Uses isolated research_history_2024/ data. No parameter fitting, order API,
live bot changes or modifications to historical_data/ CSVs.
"""
import argparse
from pathlib import Path

import backtest_v8_walkforward as data_source
from backtest_v12_hourly_trend import DAY_MS, HOUR_MS, aggregate_hourly
import backtest_v12_hourly_trend as v12
import backtest_v13_hourly_reversion as v13
import backtest_v15_fourhour_squeeze as v15
import backtest_v16_daily_momentum as v16
import backtest_v17_failed_breakout as v17
from download_research_2024 import FOLDER, stamp

STRATEGIES = (("V12 TREND", v12.signals, v12.run_window),
              ("V13 MEAN", v13.signals, v13.run_window),
              ("V15 SQUEEZE", v15.signals, v12.run_window),
              ("V17 FEHLAUSBRUCH", v17.signals, v13.run_window))
TEST_START = stamp("2024-10-02")
TRAIN_START = TEST_START - 180 * DAY_MS
TEST_END = TEST_START + 90 * DAY_MS - HOUR_MS
TRAIN_END = TEST_START - HOUR_MS


def evaluate(symbol):
    path = FOLDER / f"{symbol}_5m.csv"
    if not path.exists():
        print(symbol, "2024 CSV fehlt:", path, flush=True)
        return
    data_source.DATA_FOLDER = str(FOLDER)
    five = data_source.load_csv(symbol, "5m")
    timestamps = [int(c["timestamp"]) for c in five]
    if timestamps != sorted(set(timestamps)):
        print(symbol, "2024 CSV unsortiert oder doppelt; Abbruch.", flush=True)
        return
    expected = (stamp("2025-01-01") - stamp("2024-01-01")) // 300_000
    print(symbol, f"2024 Daten {len(five)}/{expected} 5m", flush=True)
    if len(five) < expected * .95 or five[0]["timestamp"] > TRAIN_START - 31 * DAY_MS:
        print(symbol, "Mindestens 95% Daten und 31 Tage Vorlauf erforderlich; kein Ergebnis.", flush=True)
        return
    hour = aggregate_hourly(five)
    if not hour or hour[-1]["timestamp"] < TEST_END:
        print(symbol, "Keine vollstaendigen Daten bis Testende; kein Ergebnis.", flush=True)
        return
    windows = (("TRAIN", TRAIN_START, TRAIN_END, 180 * 24),
               ("TEST", TEST_START, TEST_END, 90 * 24))
    print("TRAIN: 2024-04-05 bis 2024-10-01, TEST: 2024-10-02 bis 2024-12-30 UTC",
          flush=True)
    for name, make_signals, run in STRATEGIES:
        sig = make_signals(hour)
        for label, lo, hi, total in windows:
            covered = sum(lo <= b["timestamp"] <= hi for b in hour)
            if covered < total * .95:
                print(name, label, f"Daten {covered}/{total} (<95%); kein Ergebnis", flush=True)
                continue
            r = run(hour, sig, lo, hi)
            print(f"{name} {label}: Trades {r['count']}, PF {r['pf']:.2f}, "
                  f"Netto {r['net']:+.2f} USDT, Gebuehren {r['fees']:.2f}, "
                  f"Funding {r['funding']:.2f}, realisierter DD {r['realized_dd_pct']:.2f}%",
                  flush=True)
    days = v16.daily(hour)
    day_signals = v16.signals(days)
    for label, lo, hi, total in (("TRAIN", TRAIN_START, TRAIN_END, 180),
                                 ("TEST", TEST_START, TEST_END, 90)):
        last_day = hi - hi % DAY_MS
        covered = sum(lo <= b["timestamp"] <= last_day for b in days)
        if covered < total * .95:
            print("V16 LONG/CASH", label, f"volle Tage {covered}/{total} (<95%); kein Ergebnis",
                  flush=True)
            continue
        r = v16.run_window(days, day_signals, lo, last_day)
        hold = v16.buy_hold_baseline(days, lo, last_day)
        print(f"V16 LONG/CASH {label}: Trades {r['count']}, PF {r['pf']:.2f}, "
              f"Netto {r['net']:+.2f} USDT, Buy-and-Hold "
              f"{hold if hold is not None else float('nan'):+.2f} USDT, "
              f"Gebuehren {r['fees']:.2f}, Funding {r['funding']:.2f}", flush=True)


def main():
    parser = argparse.ArgumentParser(description="2024 backtest frozen strategies; no orders")
    parser.add_argument("--symbols", nargs="+", default=["ADAUSDT"])
    args = parser.parse_args()
    print("2024 RESEARCH – Regeln unveraendert; Original-CSV und Bots unberuehrt.", flush=True)
    for symbol in [s.upper() for s in args.symbols]:
        evaluate(symbol)
    print("FERTIG – 2024 ist ein weiterer historischer Test, kein Live-Nachweis.", flush=True)


if __name__ == "__main__":
    main()
