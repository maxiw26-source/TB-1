"""Backfill only missing internal 5m candles from the Bitunix history API.

Existing OHLCV rows remain authoritative. Before the first replacement a
one-time original-file backup is written; updates use atomic replacement.
"""
import argparse
import csv
import math
import os
import shutil
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from historical_data import REQUEST_DELAY_SECONDS, normalize, request_klines

STEP = 300_000
LIMIT = 200
FIELDS = ("timestamp", "datetime", "open", "high", "low", "close", "volume")


def read_rows(path):
    with path.open(newline="", encoding="utf-8") as stream:
        rows = list(csv.DictReader(stream))
    timestamps = [int(r["timestamp"]) for r in rows]
    if timestamps != sorted(set(timestamps)):
        raise ValueError(f"Unsortierte/doppelte Zeitstempel: {path}")
    if any(t % STEP for t in timestamps):
        raise ValueError(f"Nicht ausgerichtete 5m-Kerzen: {path}")
    return {int(r["timestamp"]): r for r in rows}


def missing_times(timestamps):
    missing = []
    for left, right in zip(timestamps, timestamps[1:]):
        missing.extend(range(left + STEP, right, STEP))
    return missing


def batches(times):
    """Group missing timestamps into API windows of at most 200 5m slots."""
    if not times:
        return
    first = last = times[0]
    for ts in times[1:]:
        if ts - first >= LIMIT * STEP:
            yield first, last
            first = ts
        last = ts
    yield first, last


def valid_candle(c, expected):
    try:
        ts = int(c["timestamp"])
        op, hi, lo, close, vol = (float(c[k]) for k in
                                   ("open", "high", "low", "close", "volume"))
    except (KeyError, ValueError, TypeError):
        return False
    return (ts in expected and ts % STEP == 0 and
            all(math.isfinite(x) for x in (op, hi, lo, close, vol)) and
            op > 0 and close > 0 and hi >= max(op, close, lo) and
            lo <= min(op, close) and vol >= 0)


def atomic_save(path, rows):
    backup = path.with_name(path.name + ".before-gap-repair.bak")
    if not backup.exists():
        shutil.copy2(path, backup)
        print("Sicherung:", backup, flush=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="",
                                         dir=path.parent, prefix=path.name + ".",
                                         suffix=".tmp", delete=False) as stream:
            temp_path = Path(stream.name)
            writer = csv.DictWriter(stream, fieldnames=FIELDS)
            writer.writeheader()
            for ts in sorted(rows):
                r = rows[ts]
                writer.writerow({"timestamp": ts,
                                 "datetime": datetime.fromtimestamp(
                                     ts / 1000, timezone.utc).isoformat(),
                                 **{key: r[key] for key in FIELDS[2:]}})
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and temp_path.exists():
            temp_path.unlink()


def repair(symbol, max_requests):
    path = Path("historical_data") / f"{symbol}_5m.csv"
    if not path.exists():
        print(symbol, "Datei fehlt:", path, flush=True)
        return
    rows = read_rows(path)
    missing = missing_times(sorted(rows))
    if not missing:
        print(symbol, "keine internen Luecken", flush=True)
        return
    chunks = list(batches(missing))
    print(symbol, "fehlende 5m-Kerzen:", len(missing), "| API-Fenster:",
          len(chunks), flush=True)
    added = 0
    for number, (start, end) in enumerate(chunks[:max_requests], 1):
        expected = set(range(start, end + STEP, STEP)) - rows.keys()
        try:
            response = request_klines(symbol, "5m", start, end)
        except (OSError, RuntimeError, TimeoutError) as exc:
            print("Download unterbrochen bei Fenster", number, str(exc), flush=True)
            break
        if response.get("code") != 0:
            print("API-Fehler bei Fenster", number, response.get("code"), flush=True)
            break
        for item in response.get("data") or []:
            try:
                c = normalize(item)
            except (KeyError, ValueError, TypeError):
                continue
            if valid_candle(c, expected) and c["timestamp"] not in rows:
                rows[c["timestamp"]] = c
                added += 1
        if number % 25 == 0 or number == min(len(chunks), max_requests):
            print(symbol, "Fortschritt:", number, "Fenster,", added,
                  "Kerzen nachgeladen", flush=True)
        time.sleep(REQUEST_DELAY_SECONDS)
    if added:
        atomic_save(path, rows)
    left = len(missing_times(sorted(rows)))
    print(symbol, "ergänzt:", added, "| weiterhin fehlend:", left, flush=True)
    if left:
        print("Erneut ausfuehren, falls API und Historie die Luecken anbieten.", flush=True)


def main():
    parser = argparse.ArgumentParser(description="Repair internal 5m gaps only")
    parser.add_argument("--symbols", nargs="+", default=["ETHUSDT", "ADAUSDT"])
    parser.add_argument("--max-requests", type=int, default=150)
    args = parser.parse_args()
    if not 1 <= args.max_requests <= 500:
        parser.error("--max-requests must be between 1 and 500")
    for symbol in (s.upper() for s in args.symbols):
        repair(symbol, args.max_requests)
    print("FERTIG – vorhandene OHLCV-Kerzen wurden nicht ersetzt.", flush=True)


if __name__ == "__main__":
    main()
