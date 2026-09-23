"""V9 exploratory 4h regime-filter comparison; no live orders.

Uses the existing 5m Bollinger/RSI/VWAP strategy unchanged. A 4h
(48-bar) past-only drift gate rejects entries while the market has made
a large directional move relative to CURRENT 5m ATR. Exit handling,
fees/slippage, position size and cooldown remain identical.

Historical TEST data has already been examined in previous V9 research.
Results are diagnostic/sensitivity evidence, NOT fresh independent OOS.
"""
import argparse
from pathlib import Path

from backtest_v8_walkforward import load_csv
from backtest_v9 import CANDLE_MS, print_result, run_window, write_trades
from strategy_v9 import indicators


LOOKBACK_BARS = 48
REGIMES = (
    ("v9_original", None),
    ("four_hour_drift_2atr", 2.0),
    ("four_hour_drift_1atr", 1.0),
)


def entry_mask(candles, features, max_drift_atr, lookback=LOOKBACK_BARS):
    """Gate on 48 PRECEDING 5m bars; no future prices or partial 1h bars.

    features[i] contains only information through closed candle i.
    Signal at i can only enter at i+1's open in backtest_v9.
    Reject gaps in the 4h lookback, rather than comparing distant candles.
    """
    mask = [False] * len(candles)
    for i in range(lookback, len(candles)):
        values = features[i]
        if values is None or values["atr"] <= 0:
            continue
        if (
            int(candles[i]["timestamp"])
            - int(candles[i - lookback]["timestamp"])
            != lookback * CANDLE_MS
        ):
            continue
        drift = abs(
            float(candles[i]["close"])
            - float(candles[i - lookback]["close"])
        )
        mask[i] = drift <= max_drift_atr * values["atr"]
    return mask


def main():
    parser = argparse.ArgumentParser(
        description="V9 4h drift regime research; backtest only"
    )
    parser.add_argument(
        "--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "ADAUSDT"]
    )
    parser.add_argument("--train-days", type=int, default=240)
    parser.add_argument("--test-days", type=int, default=120)
    args = parser.parse_args()
    if args.train_days < 30 or args.test_days < 30:
        parser.error("train/test muessen jeweils mindestens 30 Tage sein")

    print("V9 MARKTPHASEN-FILTER – NUR FORSCHUNGS-BACKTEST", flush=True)
    print(
        "Original vs. 4h Kursdrift <= 2 ATR vs. <= 1 ATR; "
        "sonst gleiche V9 Regeln.",
        flush=True,
    )
    print(
        "Ergebnisse auf bereits analysierten Daten sind KEIN neuer "
        "unabhaengiger OOS-Test.",
        flush=True,
    )

    day_ms = 86_400_000
    for symbol in [s.upper() for s in args.symbols]:
        print(f"\n{'=' * 38}\n{symbol}\n{'=' * 38}", flush=True)
        candles = load_csv(symbol, "5m")
        if len(candles) < 500:
            print("Zu wenige 5m-Kerzen", flush=True)
            continue
        end = int(candles[-1]["timestamp"])
        test_start = end - args.test_days * day_ms
        train_start = test_start - args.train_days * day_ms
        if int(candles[0]["timestamp"]) > train_start - 2 * day_ms:
            print("Zu wenig Historie: bitte historische Daten erweitern.", flush=True)
            continue

        features = indicators(candles)
        for name, threshold in REGIMES:
            mask = (
                None
                if threshold is None
                else entry_mask(candles, features, threshold)
            )
            print(f"\n{name}", flush=True)
            train = run_window(
                candles, features, train_start, test_start - CANDLE_MS,
                entry_allowed=mask,
            )
            test = run_window(
                candles, features, test_start, end, entry_allowed=mask
            )
            print_result("TRAIN", train)
            print_result("TEST  ", test)
            folder = Path("v9_results") / "regime"
            write_trades(folder / f"{symbol}_{name}_train.csv", train)
            write_trades(folder / f"{symbol}_{name}_test.csv", test)

    print(
        "\nFERTIG – CSVs in v9_results/regime. "
        "Nur historische Simulation; keine Live-Aenderung.",
        flush=True,
    )


if __name__ == "__main__":
    main()
