"""Read-only audit of 5m history and its complete 1h groups."""
import argparse
from datetime import datetime, timezone

from backtest_v8_walkforward import load_csv
from backtest_v12_hourly_trend import FIVE_MS, HOUR_MS, DAY_MS, aggregate_hourly


def dt(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def inspect(candles, days=270):
    """Return gaps inside the requested window, with a 200h warmup margin."""
    if not candles:
        return None
    end = int(candles[-1]["timestamp"])
    requested = end - days * DAY_MS - 200 * HOUR_MS
    recent = [c for c in candles if int(c["timestamp"]) >= requested]
    missing = []
    invalid = []
    for previous, current in zip(recent, recent[1:]):
        p, c = int(previous["timestamp"]), int(current["timestamp"])
        if c - p > FIVE_MS:
            missing.append((p + FIVE_MS, c - FIVE_MS, (c - p) // FIVE_MS - 1))
        if c - p <= 0 or (c - p) % FIVE_MS != 0:
            invalid.append((p, c))
    if not recent:
        return None
    hourly = aggregate_hourly(recent)
    hour_times = {int(c["timestamp"]) for c in hourly}
    test_end = end // HOUR_MS * HOUR_MS - HOUR_MS
    split = test_end - 90 * DAY_MS
    start = split - 180 * DAY_MS
    windows = {}
    for name, a, b in (("TRAIN", start, split), ("TEST", split, test_end)):
        hours = range(a, b, HOUR_MS)
        windows[name] = (sum(h in hour_times for h in hours), len(hours))
    return {"from": recent[0]["timestamp"], "to": recent[-1]["timestamp"],
            "gaps": missing, "invalid": invalid, "windows": windows,
            "rows": len(recent)}


def main():
    parser = argparse.ArgumentParser(description="Inspect stored 5m candles; read-only")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "ADAUSDT"])
    args = parser.parse_args()
    for symbol in (s.upper() for s in args.symbols):
        try:
            candles = load_csv(symbol, "5m")
        except FileNotFoundError:
            print(symbol, "5m datei fehlt", flush=True)
            continue
        report = inspect(candles)
        if report is None:
            print(symbol, "keine Daten", flush=True)
            continue
        gaps = sorted(report["gaps"], key=lambda g: g[2], reverse=True)
        print("\n", symbol, "5m:", report["rows"], "Kerzen;",
              dt(report["from"]), "bis", dt(report["to"]), flush=True)
        print("Interne Luecken:", len(gaps), "| Fehlende 5m-Kerzen:",
              sum(g[2] for g in gaps), "| Ungueltige Zeitabstaende:",
              len(report["invalid"]), flush=True)
        for name, (valid, expected) in report["windows"].items():
            print(name, "vollstaendige 1h-Kerzen:", valid, "/", expected, flush=True)
        for first, last, count in gaps[:5]:
            print("Luecke:", dt(first), "bis", dt(last), "|", count,
                  "fehlende 5m-Kerzen", flush=True)
        for first, last in report["invalid"][:3]:
            print("Ungueltiger Abstand:", dt(first), "zu", dt(last), flush=True)
    print("FERTIG – Nur gelesen, keine CSV veraendert.", flush=True)


if __name__ == "__main__":
    main()
