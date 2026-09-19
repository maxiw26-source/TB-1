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

LIVE_TEST_SYMBOL = "BTCUSDT"
LIVE_TEST_QTY = "0.0001"
LIVE_TEST_MAX_ORDERS = 1
LIVE_TEST_ORDER_COUNT = 0

PENDING_SETUPS = {}
LAST_PROCESSED_CANDLE = {}
FORCE_TEST_SETUP = False


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

def pending_entry_reached(pending_setup, candle):
    setup = pending_setup["setup"]
    entry = float(setup["entry"])

    low = float(candle["low"])
    high = float(candle["high"])

    return low <= entry <= high

def pending_setup_expired(pending_setup, current_candle_ts):
    setup = pending_setup["setup"]

    fvg_timestamp = int(setup["fvg_timestamp"])

    expiry_candles = int(
        getattr(config, "SETUP_EXPIRY_CANDLES", 5)
    )

    expiry_ms = expiry_candles * 60_000

    return current_candle_ts > (
        fvg_timestamp + expiry_ms
    )

def pending_setup_invalidated(pending_setup, candle):
    setup = pending_setup["setup"]
    signal = pending_setup["signal"]

    stop = float(setup["stop"])
    low = float(candle["low"])
    high = float(candle["high"])

    if signal == "PENDING_LONG":
        return low <= stop

    if signal == "PENDING_SHORT":
        return high >= stop

    return True

def get_last_closed_1m_candle(symbol):
    candles = get_klines(
        symbol,
        "1m",
        limit=5,
    )

    now_ms = int(time.time() * 1000)

    closed_candles = [
        candle
        for candle in candles
        if candle["timestamp"] + 60_000 <= now_ms
    ]

    if not closed_candles:
        return None

    return closed_candles[-1]

def build_order_plan(pending_setup, qty="0.0001"):
    setup = pending_setup["setup"]
    signal = pending_setup["signal"]

    entry = float(setup["entry"])
    stop = float(setup["stop"])

    risk = abs(entry - stop)

    if signal == "PENDING_LONG":
        side = "BUY"
        tp1 = entry + risk
        tp2 = entry + (risk * 1.2)

    elif signal == "PENDING_SHORT":
        side = "SELL"
        tp1 = entry - risk
        tp2 = entry - (risk * 1.2)

    else:
        raise ValueError("Unbekanntes Pending-Signal")

    return {
        "symbol": pending_setup.get("symbol"),
        "side": side,
        "qty": str(qty),
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "risk_distance": risk,
    }

def confirm_live_order(order_data):
    print("")
    print("LIVE-ORDER BEREIT:")
    print(order_data)

    answer = input(
        "Order wirklich senden? (JA/NEIN): "
    ).strip().upper()

    return answer == "JA"

def review_order_plan(pending_setup, qty="0.0001"):
    plan = build_order_plan(
        pending_setup,
        qty=qty,
    )

    confirmed = confirm_live_order(plan)

    if confirmed:
        print("")
        print("ORDER MANUELL BESTÄTIGT")
        print(plan)
    else:
        print("")
        print("ORDER ABGELEHNT")

    return confirmed

if __name__ == "__main__":
    print("LSOB V7 Live-Paper")
    print("LIVE_TRADING:", LIVE_TRADING)

    while True:
        try:
            for symbol in SYMBOLS:
                result = analyze_symbol(symbol)

                if symbol in PENDING_SETUPS:
                    candle = get_last_closed_1m_candle(symbol)

                    if candle is not None:
                        candle_ts = candle["timestamp"]

                        if pending_setup_invalidated(
                            PENDING_SETUPS[symbol],
                            candle,
                        ):
                            print("")
                            print("PENDING-SETUP UNGÜLTIG:")
                            print(symbol)

                            del PENDING_SETUPS[symbol]
                            continue

                        if pending_setup_expired(
                            PENDING_SETUPS[symbol],
                            candle_ts,
                        ):
                            print("")
                            print("PENDING-SETUP ABGELAUFEN:")
                            print(symbol)

                            del PENDING_SETUPS[symbol]
                            continue

                        if LAST_PROCESSED_CANDLE.get(symbol) != candle_ts:
                            LAST_PROCESSED_CANDLE[symbol] = candle_ts

                        if pending_entry_reached(
                            PENDING_SETUPS[symbol],
                            candle,
                        ):
                            print("")
                            print("V7 ENTRY ERREICHT:")
                            print(symbol)
                            print(PENDING_SETUPS[symbol])

                            pending = PENDING_SETUPS[symbol].copy()
                            pending["symbol"] = symbol

                            review_order_plan(
                                pending,
                                qty="0.0001",
                            )

                            del PENDING_SETUPS[symbol]

                print("")
                print(symbol)

                if result is None:
                    print("Signal: None")
                    continue

                if str(
                    result.get("signal", "")
                ).startswith("PENDING_"):

                    if symbol not in PENDING_SETUPS:
                        PENDING_SETUPS[symbol] = {
                            "created_at": datetime.now(
                                timezone.utc
                            ).isoformat(),
                            "signal": result.get("signal"),
                            "setup": result["indicators"]["setup"],
                        }

                        print(
                            "NEUES PENDING-SETUP GESPEICHERT:"
                        )
                        print(PENDING_SETUPS[symbol])

                        print("SETUP KEYS:", result.keys())
                print(
                    "Signal:",
                    result.get("signal"),
                )

                print(
                    "Grund:",
                    result.get("reason"),
                )

            print("")
            print(
                "Warte",
                POLL_SECONDS,
                "Sekunden..."
            )

            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            print("Bot beendet.")
            break

        except Exception as e:
            print("Fehler:", e)
            time.sleep(POLL_SECONDS)