import time
from datetime import datetime, timezone

import requests

import config

import os

import hashlib
import uuid

from strategy_v7 import calculate_signal


BASE_URL = "https://fapi.bitunix.com"

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
]

LIVE_TRADING = False

BITUNIX_API_KEY = os.getenv("BITUNIX_API_KEY")
BITUNIX_SECRET_KEY = os.getenv("BITUNIX_SECRET_KEY")

LIVE_MAX_BALANCE_USDT = 10.0
LIVE_RISK_PER_TRADE_PERCENT = 0.25
LIVE_LEVERAGE = 2
LIVE_MAX_DAILY_LOSS_USDT = 0.50
LIVE_MAX_OPEN_POSITIONS = 1

POLL_SECONDS = 30

START_BALANCE = float(
    getattr(config, "START_BALANCE", 10000.0)
)

def create_signature(api_key, secret_key, timestamp, nonce, body=""):
    first_hash = hashlib.sha256(
        (
            nonce
            + timestamp
            + api_key
            + body
        ).encode()
    ).hexdigest()

    signature = hashlib.sha256(
        (
            first_hash
            + secret_key
        ).encode()
    ).hexdigest()

    return signature

def get_auth_headers():
    timestamp = str(int(time.time() * 1000))
    nonce = uuid.uuid4().hex

    signature = create_signature(
        BITUNIX_API_KEY,
        BITUNIX_SECRET_KEY,
        timestamp,
        nonce,
    )

    return {
        "api-key": BITUNIX_API_KEY,
        "sign": signature,
        "timestamp": timestamp,
        "nonce": nonce,
        "Content-Type": "application/json",
    }

def get_klines(
    symbol,
    interval,
    limit=300,
):
    url = (
        BASE_URL
        + "/api/v1/futures/market/kline"
    )

    params = {
        "symbol": symbol,
        "interval": interval,
        "limit": limit,
    }

    response = requests.get(
        url,
        params=params,
        timeout=10,
    )

    response.raise_for_status()

    result = response.json()

    if result.get("code") != 0:
        raise RuntimeError(result)

    data = result.get("data", [])

    candles = []

    for item in data:
        candles.append({
            "timestamp": int(item["time"]),
            "open": float(item["open"]),
            "high": float(item["high"]),
            "low": float(item["low"]),
            "close": float(item["close"]),
            "volume": float(
                item.get("baseVol", 0) or 0
            ),
        })

    candles.sort(
        key=lambda candle: candle["timestamp"]
    )

    return candles

def analyze_symbol(symbol):
    entry_candles = get_klines(
        symbol,
        "1m",
        limit=300,
    )

    confirmation_candles = get_klines(
        symbol,
        "5m",
        limit=300,
    )

    trend_candles = get_klines(
        symbol,
        "15m",
        limit=300,
    )

    if symbol == "ETHUSDT":
        swing_lookback = 7
        sweep_to_bos_candles = 12
        bos_to_fvg_candles = 4
        min_fvg_atr_ratio = 0.05
    else:
        swing_lookback = 10
        sweep_to_bos_candles = 8
        bos_to_fvg_candles = 4
        min_fvg_atr_ratio = 0.10

    result = calculate_signal(
        entry_candles,
        confirmation_candles,
        trend_candles,
        swing_lookback,
        sweep_to_bos_candles,
        bos_to_fvg_candles,
        min_fvg_atr_ratio,
    )

    return result

def get_account_balance():
    url = BASE_URL + "/api/v1/cp/asset/query"

    headers = get_auth_headers()

    response = requests.get(
        url,
        headers=headers,
        timeout=10,
    )

    response.raise_for_status()

    return response.json()

if __name__ == "__main__":
    print("LSOB V7 Live-Paper")
    print("LIVE_TRADING:", LIVE_TRADING)

    for symbol in SYMBOLS:
        result = analyze_symbol(symbol)

        print("")
        print(symbol)

        if result is None:
            print("Signal: None")
            continue

        print(
            "Signal:",
            result.get("signal"),
        )

        print(
            "Grund:",
            result.get("reason"),
        )

        print(
            "API Key geladen:",
             bool(BITUNIX_API_KEY)
)

        print(
             "Secret geladen:",
             bool(BITUNIX_SECRET_KEY)
)

print("")
print("Account-Test:")

try:
    account = get_account_balance()
    print(account)
except Exception as e:
    print("Fehler:", e)