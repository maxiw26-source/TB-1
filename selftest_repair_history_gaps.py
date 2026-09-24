"""Offline test for internal gap repair and untouched original candles."""
import csv
import os
import tempfile
from pathlib import Path

import repair_history_gaps as tool


def main():
    assert list(tool.batches([300_000, 900_000])) == [(300_000, 900_000)]
    with tempfile.TemporaryDirectory() as folder:
        old = Path.cwd()
        try:
            os.chdir(folder)
            Path("historical_data").mkdir()
            path = Path("historical_data/TEST_5m.csv")
            with path.open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=tool.FIELDS)
                writer.writeheader()
                for ts in (0, 600_000, 1_200_000):
                    writer.writerow({"timestamp": ts, "datetime": "original",
                                     "open": 100, "high": 101, "low": 99,
                                     "close": 100, "volume": 10})
            original = path.read_bytes()
            saved = tool.request_klines
            saved_wait = tool.REQUEST_DELAY_SECONDS
            try:
                tool.REQUEST_DELAY_SECONDS = 0
                def fake_api(symbol, interval, start, end):
                    assert (symbol, interval, start, end) == (
                        "TEST", "5m", 300_000, 900_000)
                    return {"code": 0, "data": [
                        {"time": ts, "open": "100", "high": "101",
                         "low": "99", "close": "100", "baseVol": "10"}
                        for ts in (300_000, 600_000, 900_000)]}
                tool.request_klines = fake_api
                tool.repair("TEST", 5)
            finally:
                tool.request_klines = saved
                tool.REQUEST_DELAY_SECONDS = saved_wait
            rows = tool.read_rows(path)
            assert sorted(rows) == [0, 300_000, 600_000, 900_000, 1_200_000]
            assert rows[600_000]["close"] == "100"
            assert tool.missing_times(sorted(rows)) == []
            assert path.with_name(path.name + ".before-gap-repair.bak").read_bytes() == original
        finally:
            os.chdir(old)
    print("GAP-REPAIR SELFTEST ERFOLGREICH: nur fehlende Kerzen, Backup, atomisch")


if __name__ == "__main__":
    main()
