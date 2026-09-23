"""V9 vs. V9.1: 15m ADX(14) < 20, research-only comparison.

The underlying V9 signal, stops, VWAP target, size, taker fees and slippage
are unchanged. Both train and test have been viewed in earlier V9 work;
this is a sensitivity check, NOT an independent out-of-sample validation.
No live-trading modules or API credentials are imported.
"""
import argparse
from pathlib import Path

from backtest_v8_walkforward import load_csv
from backtest_v9 import CANDLE_MS, print_result, run_window, write_trades
from strategy_v9 import indicators


ADX_LIMIT = 20.0
FIFTEEN_MIN_MS = 900_000
CONTIGUOUS_BARS = 48  # warm-up and protection against gaps in 15m history


def entry_mask(candles_5m, candles_15m, *, limit=ADX_LIMIT):
    """Gate the 5m signal with the latest fully CLOSED 15m candle.

    Candle timestamps are opening timestamps. A 5m signal at 12:10 is
    assessed after its 12:15 close and can use the 12:00 15m candle, but
    never the 12:15 candle. The V9 engine enters at the next 5m open.
    Missing, stale, duplicate or discontinuous 15m bars fail closed.
    """
    mask = [False] * len(candles_5m)
    if not candles_15m:
        return mask
    times = [int(c["timestamp"]) for c in candles_15m]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("15m candles must have unique, ascending opening times")
    adx_values = indicators(candles_15m)
    contiguous = [1] * len(times)
    for j in range(1, len(times)):
        if times[j] - times[j - 1] == FIFTEEN_MIN_MS:
            contiguous[j] = contiguous[j - 1] + 1

    j = -1
    for i, candle in enumerate(candles_5m):
        signal_close = int(candle["timestamp"]) + CANDLE_MS
        while j + 1 < len(times) and times[j + 1] + FIFTEEN_MIN_MS <= signal_close:
            j += 1
        if j < 0 or contiguous[j] < CONTIGUOUS_BARS:
            continue
        if signal_close >= times[j] + 2 * FIFTEEN_MIN_MS:
            continue  # latest closed 15m bar is stale
        values = adx_values[j]
        mask[i] = values is not None and values["adx"] < limit
    return mask


def main():
    parser = argparse.ArgumentParser(description="V9 / V9.1 ADX 15m research comparison")
    parser.add_argument("--symbols", nargs="+", default=["ETHUSDT", "ADAUSDT"])
    parser.add_argument("--train-days", type=int, default=240)
    parser.add_argument("--test-days", type=int, default=120)
    args = parser.parse_args()
    if args.train_days < 30 or args.test_days < 30:
        parser.error("train/test must each be at least 30 days")

    print("V9.1 ADX15M – NUR HISTORISCHER BACKTEST, KEINE ORDERS", flush=True)
    print("V9 original vs. ADX(14) 15m < 20; gleiche V9-Kosten und Exits.", flush=True)
    print("TEST wurde bereits in V9 analysiert: KEIN neuer unabhaengiger Test.", flush=True)
    print("Funding und echte Orderbuch-Fills sind im V9-Backtest nicht modelliert.", flush=True)
    day_ms = 86_400_000
    for symbol in [s.upper() for s in args.symbols]:
        print(f"\n{symbol}", flush=True)
        try:
            candles = load_csv(symbol, "5m")
            candles_15m = load_csv(symbol, "15m")
        except FileNotFoundError as exc:
            print(f"Fehlende historische Datei: {exc}", flush=True)
            continue
        if len(candles) < 500 or len(candles_15m) < CONTIGUOUS_BARS:
            print("Zu wenige 5m- oder 15m-Kerzen.", flush=True)
            continue
        end = int(candles[-1]["timestamp"])
        test_start = end - args.test_days * day_ms
        train_start = test_start - args.train_days * day_ms
        if int(candles[0]["timestamp"]) > train_start - 2 * day_ms:
            print("Zu wenig 5m-Historie fuer Train + Warmup.", flush=True)
            continue
        if int(candles_15m[0]["timestamp"]) > train_start - 2 * day_ms:
            print("Zu wenig 15m-Historie fuer Train + Warmup.", flush=True)
            continue

        features = indicators(candles)
        mask = entry_mask(candles, candles_15m)
        for name, gate in (("v9_original", None), ("v9_1_adx15m", mask)):
            print(name, flush=True)
            train = run_window(candles, features, train_start,
                               test_start - CANDLE_MS, entry_allowed=gate)
            test = run_window(candles, features, test_start,
                              end, entry_allowed=gate)
            print_result("TRAIN", train)
            print_result("TEST ", test)
            output = Path("v9_results") / "adx15m"
            write_trades(output / f"{symbol}_{name}_train.csv", train)
            write_trades(output / f"{symbol}_{name}_test.csv", test)
    print("FERTIG – CSVs in v9_results/adx15m; keine Live-Aenderung.", flush=True)


if __name__ == "__main__":
    main()
