import argparse
import csv
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone


BASE_URL = "https://fapi.bitunix.com/api/v1/futures/market/kline"

DATA_FOLDER = "historical_data"

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
    "SOLUSDT",
]

INTERVALS = [
    "1m",
    "5m",
    "15m",
]

DAYS_TO_DOWNLOAD = 1800
REQUEST_LIMIT = 200
REQUEST_DELAY_SECONDS = 0.25

INTERVAL_MS = {
    "1m": 60_000,
    "5m": 300_000,
    "15m": 900_000,
}


def to_ms(value):
    return int(value.timestamp() * 1000)


def request_klines(
    symbol,
    interval,
    start_time,
    end_time,
):
    params = {
        "symbol": symbol,
        "interval": interval,
        "startTime": int(start_time),
        "endTime": int(end_time),
        "limit": REQUEST_LIMIT,
    }

    url = BASE_URL + "?" + urllib.parse.urlencode(params)

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
        },
    )

    last_error = None

    for attempt in range(1, 7):
        try:
            with urllib.request.urlopen(
                request,
                timeout=20,
            ) as response:
                return json.loads(
                    response.read().decode("utf-8")
                )
        except (
            urllib.error.URLError,
            TimeoutError,
            ConnectionError,
            OSError,
        ) as exc:
            last_error = exc

            if attempt >= 6:
                break

            wait_seconds = min(
                30,
                2 * attempt,
            )

            print(
                "Netzwerkfehler – neuer Versuch",
                attempt,
                "/ 6 in",
                wait_seconds,
                "Sekunden:",
                exc,
            )

            time.sleep(
                wait_seconds
            )

    raise RuntimeError(
        "Historischer Download nach 6 "
        "Versuchen abgebrochen"
    ) from last_error


def normalize(item):
    return {
        "timestamp": int(item["time"]),
        "open": float(item["open"]),
        "high": float(item["high"]),
        "low": float(item["low"]),
        "close": float(item["close"]),
        "volume": float(
            item.get("baseVol", 0)
        ),
    }


def download(
    symbol,
    interval,
    start_time,
    end_time,
):
    interval_ms = INTERVAL_MS[interval]

    candles = {}
    current_start = start_time

    empty_blocks = 0
    MAX_EMPTY_BLOCKS = 20

    print("")
    print("Download:", symbol, interval)

    while current_start <= end_time:
        block_end = min(
            current_start
            + interval_ms * (REQUEST_LIMIT - 1),
            end_time,
        )

        result = request_klines(
            symbol,
            interval,
            current_start,
            block_end,
        )

        if result.get("code") != 0:
            raise RuntimeError(result)

        data = result.get("data", [])

        if not data:
            empty_blocks += 1

            print(
                "Leerer Block:",
                empty_blocks,
                "/",
                MAX_EMPTY_BLOCKS,
            )

            if empty_blocks >= MAX_EMPTY_BLOCKS:
                print(
                    "Zu viele leere Blöcke – Download wird abgebrochen."
                )
                break
        else:
            empty_blocks = 0

        for item in data:
            candle = normalize(item)
            timestamp = candle["timestamp"]

            if start_time <= timestamp <= end_time:
                candles[timestamp] = candle

        print(
            "Kerzen gespeichert:",
            len(candles),
        )

        current_start = (
            block_end
            + interval_ms
        )

        time.sleep(
            REQUEST_DELAY_SECONDS
        )

    result = list(
        candles.values()
    )

    result.sort(
        key=lambda candle: candle["timestamp"]
    )

    return result

def load_existing(symbol, interval):
    path = os.path.join(
        DATA_FOLDER,
        f"{symbol}_{interval}.csv",
    )

    if not os.path.exists(path):
        return []

    candles = []

    with open(
        path,
        "r",
        encoding="utf-8",
        newline="",
    ) as file:
        reader = csv.DictReader(file)

        for row in reader:
            candles.append({
                "timestamp": int(row["timestamp"]),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            })

    candles.sort(
        key=lambda candle: candle["timestamp"]
    )

    return candles

def save(symbol, interval, candles):
    os.makedirs(
        DATA_FOLDER,
        exist_ok=True,
    )

    path = os.path.join(
        DATA_FOLDER,
        f"{symbol}_{interval}.csv",
    )

    with open(
        path,
        "w",
        encoding="utf-8",
        newline="",
    ) as file:
        writer = csv.DictWriter(
            file,
            fieldnames=[
                "timestamp",
                "datetime",
                "open",
                "high",
                "low",
                "close",
                "volume",
            ],
        )

        writer.writeheader()

        for candle in candles:
            writer.writerow({
                "timestamp": candle["timestamp"],
                "datetime": datetime.fromtimestamp(
                    candle["timestamp"] / 1000,
                    tz=timezone.utc,
                ).isoformat(),
                "open": candle["open"],
                "high": candle["high"],
                "low": candle["low"],
                "close": candle["close"],
                "volume": candle["volume"],
            })

    print(
        "Gespeichert:",
        path,
        "|",
        len(candles),
        "Kerzen",
    )


def parse_args():
    parser = argparse.ArgumentParser(
        description="Bitunix historische Kerzendaten laden"
    )
    parser.add_argument(
        "--days",
        type=int,
        default=DAYS_TO_DOWNLOAD,
        help="Anzahl Tage historischer Daten",
    )
    parser.add_argument(
        "--symbols",
        nargs="+",
        default=SYMBOLS,
        help="Symbole, z.B. BTCUSDT ETHUSDT SOLUSDT",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    days_to_download = max(1, int(args.days))
    symbols = [symbol.upper() for symbol in args.symbols]

    end_datetime = datetime.now(
        timezone.utc
    ).replace(
        second=0,
        microsecond=0,
    )

    target_start_datetime = (
        end_datetime
        - timedelta(days=days_to_download)
    )

    target_start_time = to_ms(
        target_start_datetime
    )

    end_time = to_ms(
        end_datetime
    )

    print(
        "Ziel:",
        days_to_download,
        "Tage historische Daten",
    )

    for symbol in symbols:
        for interval in INTERVALS:

            existing = load_existing(
                symbol,
                interval,
            )

            interval_ms = INTERVAL_MS[
                interval
            ]

            if existing:
                earliest_existing = existing[0][
                    "timestamp"
                ]

                print("")
                print(
                    symbol,
                    interval,
                    "| vorhanden:",
                    len(existing),
                    "Kerzen",
                )

                if earliest_existing <= target_start_time:
                    print(
                        "Bereits genügend alte Daten vorhanden."
                    )
                    continue

                download_end = (
                    earliest_existing
                    - interval_ms
                )

                print(
                    "Lade nur fehlende ältere Daten..."
                )

                older_candles = download(
                    symbol,
                    interval,
                    target_start_time,
                    download_end,
                )

                combined = {}

                for candle in older_candles:
                    combined[
                        candle["timestamp"]
                    ] = candle

                for candle in existing:
                    combined[
                        candle["timestamp"]
                    ] = candle

                candles = list(
                    combined.values()
                )

                candles.sort(
                    key=lambda candle: candle[
                        "timestamp"
                    ]
                )

            else:
                print("")
                print(
                    symbol,
                    interval,
                    "| keine vorhandene Datei"
                )

                candles = download(
                    symbol,
                    interval,
                    target_start_time,
                    end_time,
                )

            save(
                symbol,
                interval,
                candles,
            )

    print("")
    print("ALLE DATEN AKTUALISIERT")


if __name__ == "__main__":
    main()