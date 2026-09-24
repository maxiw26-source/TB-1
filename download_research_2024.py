"""Resumable historical 2024 backfill into a NEW isolated research folder.

Does not read/write production historical_data/*.csv or place any orders.
Only exact 5m candles returned by the public API are stored, never fabricated.
"""
import argparse
import csv
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from historical_data import REQUEST_DELAY_SECONDS, normalize, request_klines
from repair_history_gaps import valid_candle

FOLDER = Path("research_history_2024")
STEP = 300_000
WINDOW = 200
FIELDS = ("timestamp", "open", "high", "low", "close", "volume")


def stamp(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=timezone.utc).timestamp() * 1000)


def load_existing(path, start, end):
    rows = {}
    if not path.exists():
        return rows
    with path.open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            ts = int(row["timestamp"])
            if ts in rows or ts % STEP or not start <= ts <= end:
                raise ValueError(f"Invalid/double/out-of-window row in {path}: {ts}")
            c = {k: float(row[k]) for k in FIELDS[1:]}
            c["timestamp"] = ts
            if not valid_candle(c, {ts}):
                raise ValueError(f"Invalid OHLCV in {path}: {ts}")
            rows[ts] = c
    return rows


def save_atomic(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         prefix=path.name + ".", suffix=".tmp",
                                         dir=path.parent, delete=False) as stream:
            tmp = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            for ts in sorted(rows):
                writer.writerow(rows[ts])
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()


def download(symbol, max_requests):
    start = stamp("2024-01-01")
    end = stamp("2025-01-01") - STEP
    path = FOLDER / f"{symbol}_5m.csv"
    rows = load_existing(path, start, end)
    requests = 0
    added = 0
    for block_start in range(start, end + 1, WINDOW * STEP):
        block_end = min(block_start + (WINDOW - 1) * STEP, end)
        expected = set(range(block_start, block_end + STEP, STEP))
        if expected <= rows.keys():
            continue
        if requests >= max_requests:
            break
        try:
            response = request_klines(symbol, "5m", block_start - STEP, block_end + STEP)
            if response.get("code") != 0:
                print(symbol, "API code", response.get("code"), "bei", block_start, flush=True)
                break
            requests += 1
            for item in response.get("data") or []:
                try:
                    candle = normalize(item)
                except (ValueError, KeyError, TypeError):
                    continue
                if valid_candle(candle, expected) and candle["timestamp"] not in rows:
                    rows[candle["timestamp"]] = candle
                    added += 1
        except (OSError, RuntimeError, TimeoutError) as exc:
            print(symbol, "Download unterbrochen:", str(exc), flush=True)
            break
        if requests % 25 == 0:
            if added:
                save_atomic(path, rows)
            print(symbol, "API-Fenster:", requests, "| Kerzen:", len(rows), flush=True)
        time.sleep(REQUEST_DELAY_SECONDS)
    if added:
        save_atomic(path, rows)
    total = (end - start) // STEP + 1
    print(symbol, f"2024 Kerzen {len(rows)}/{total} | neu {added} | "
          f"API-Fenster diesmal {requests} | Datei {path}", flush=True)
    return len(rows), total


def main():
    parser = argparse.ArgumentParser(description="Isolated 2024 historical download, no orders")
    parser.add_argument("--symbols", nargs="+", default=["ADAUSDT"])
    parser.add_argument("--max-requests", type=int, default=600)
    args = parser.parse_args()
    if args.max_requests < 1 or args.max_requests > 600:
        parser.error("--max-requests must be 1..600")
    print("2024 DOWNLOAD – getrennte research_history_2024; Original-CSV unberuehrt.", flush=True)
    for symbol in [s.upper() for s in args.symbols]:
        download(symbol, args.max_requests)


if __name__ == "__main__":
    main()
