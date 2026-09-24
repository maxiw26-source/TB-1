"""Fill only missing 2024 research candles using the public Bitunix API.

Works exclusively in research_history_2024; existing OHLCV rows remain
authoritative. Atomic saves with a one-time backup make this resumable.
"""
import argparse
import time

from download_research_2024 import FOLDER, STEP, load_existing, stamp
from historical_data import REQUEST_DELAY_SECONDS, normalize, request_klines
from repair_history_gaps import atomic_save, batches, valid_candle


def repair(symbol, max_requests):
    start = stamp("2024-01-01")
    end = stamp("2025-01-01") - STEP
    path = FOLDER / f"{symbol}_5m.csv"
    if not path.exists():
        print(symbol, "Keine Research-Datei:", path, flush=True)
        return
    rows = load_existing(path, start, end)
    missing = sorted(set(range(start, end + STEP, STEP)) - rows.keys())
    print(symbol, "2024 fehlende 5m-Kerzen:", len(missing),
          "| API-Fenster:", len(list(batches(missing))), flush=True)
    if not missing:
        return
    added = 0
    requests = 0
    empty_streak = 0
    for first, last in batches(missing):
        if requests >= max_requests:
            break
        expected = set(range(first, last + STEP, STEP)) - rows.keys()
        try:
            response = request_klines(symbol, "5m", first - STEP, last + STEP)
            if response.get("code") != 0:
                print(symbol, "API code", response.get("code"), "bei", first, flush=True)
                break
        except (OSError, RuntimeError, TimeoutError) as exc:
            print(symbol, "API unterbrochen:", exc, flush=True)
            break
        requests += 1
        current = 0
        for item in response.get("data") or []:
            try:
                candle = normalize(item)
            except (TypeError, ValueError, KeyError):
                continue
            if valid_candle(candle, expected) and candle["timestamp"] not in rows:
                rows[candle["timestamp"]] = candle
                added += 1
                current += 1
        empty_streak = empty_streak + 1 if current == 0 else 0
        if requests % 25 == 0:
            if added:
                atomic_save(path, rows)
            print(symbol, "API-Fenster", requests, "| ergaenzt", added,
                  "| zuletzt ohne Fortschritt", empty_streak, flush=True)
        if empty_streak >= 25:
            print(symbol, "25 Fenster ohne neue Daten; Pause statt unbegrenzte API-Abfragen.",
                  flush=True)
            break
        time.sleep(REQUEST_DELAY_SECONDS)
    if added:
        atomic_save(path, rows)
    left = (end - start) // STEP + 1 - len(rows)
    print(symbol, "2024 ergaenzt:", added, "| fehlend:", left,
          "| API-Fenster diesmal:", requests, flush=True)
    if left:
        print("Erneut ausfuehren, sofern API die fehlenden Kerzen anbietet.", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Research 2024 gap repair only; no orders")
    parser.add_argument("--symbols", nargs="+", default=["ADAUSDT"])
    parser.add_argument("--max-requests", type=int, default=600)
    args = parser.parse_args()
    if not 1 <= args.max_requests <= 600:
        parser.error("--max-requests must be 1..600")
    print("2024 RESEARCH REPAIR – bestehende OHLCV unberuehrt; Sicherung + atomisch.",
          flush=True)
    for symbol in [s.upper() for s in args.symbols]:
        repair(symbol, args.max_requests)
    print("FERTIG – Live-Bots und historical_data unberuehrt.", flush=True)


if __name__ == "__main__":
    main()
