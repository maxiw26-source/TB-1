"""Read-only probe for available Bitunix historical 5m bars.

Samples isolated 200-bar windows in older years without writing, repairing,
replacing, or backfilling any existing CSV. No orders or credentials.
"""
import argparse
import time
from datetime import datetime, timezone

from historical_data import REQUEST_DELAY_SECONDS, normalize, request_klines

STEP = 300_000
COUNT = 200
DATES = ("2024-01-15", "2024-07-15", "2025-01-15", "2025-06-15")


def probe(symbol, day):
    start = int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)
    end = start + (COUNT - 1) * STEP
    response = request_klines(symbol, "5m", start - STEP, end + STEP)
    if response.get("code") != 0:
        return f"API code={response.get('code')}"
    actual = sorted({normalize(item)["timestamp"] for item in response.get("data") or []
                     if start <= int(item.get("time", -1)) <= end})
    if not actual:
        return "0/200 verfuegbar"
    expected = set(range(start, end + STEP, STEP))
    contiguous = len(actual) == COUNT and set(actual) == expected
    return f"{len(actual)}/200 | geschlossenes Fenster: {'JA' if contiguous else 'NEIN'}"


def main():
    parser = argparse.ArgumentParser(description="Probe older historical data, read only")
    parser.add_argument("--symbols", nargs="+", default=["BTCUSDT", "ETHUSDT", "ADAUSDT"])
    args = parser.parse_args()
    print("HISTORIE-PROBE: nur oeffentliche API lesen; keine CSV geaendert.", flush=True)
    for symbol in [s.upper() for s in args.symbols]:
        for day in DATES:
            try:
                result = probe(symbol, day)
            except (OSError, RuntimeError, ValueError, KeyError) as exc:
                result = f"Fehler: {exc}"
            print(f"{symbol} {day}: {result}", flush=True)
            time.sleep(REQUEST_DELAY_SECONDS)
    print("FERTIG – keine CSV geaendert, keine Orders.", flush=True)


if __name__ == "__main__":
    main()
