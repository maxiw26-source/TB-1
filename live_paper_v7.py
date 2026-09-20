import csv
import hashlib
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import requests

import config
from strategy_v7 import calculate_signal
from bot_runtime import (
    approve_order,
    create_approval,
    load_runtime_state,
    log_event,
    read_approval,
    reject_order,
    save_runtime_state,
)


BASE_URL = "https://fapi.bitunix.com"

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
]

POLL_SECONDS = 30

STATE_FILE = Path("runtime_state.json")
EVENT_LOG_FILE = Path("trade_events.csv")
APPROVAL_FILE = Path("pending_approval.json")

LIVE_QTY_BY_SYMBOL = {
    "BTCUSDT": "0.0001",
    "ETHUSDT": "0.003",
}

PENDING_SETUPS = {}
LAST_PROCESSED_CANDLE = {}
OPEN_POSITIONS = {}
LAST_SETUP_ID = {}


def load_env_file(path=".env"):
    env_path = Path(path)

    if not env_path.exists():
        return

    for raw_line in env_path.read_text(
        encoding="utf-8"
    ).splitlines():
        line = raw_line.strip()

        if (
            not line
            or line.startswith("#")
            or "=" not in line
        ):
            continue

        key, value = line.split("=", 1)

        key = key.strip()
        value = value.strip().strip('"').strip("'")

        if key and key not in os.environ:
            os.environ[key] = value


load_env_file()

BITUNIX_API_KEY = os.getenv("BITUNIX_API_KEY")
BITUNIX_SECRET_KEY = os.getenv("BITUNIX_SECRET_KEY")

LIVE_TRADING = os.getenv(
    "LIVE_TRADING",
    "false",
).strip().lower() in {
    "1",
    "true",
    "yes",
    "ja",
}


def utc_now_iso():
    return datetime.now(
        timezone.utc
    ).isoformat()


def log_event(
    event,
    symbol="",
    details=None,
):
    row = {
        "timestamp": utc_now_iso(),
        "event": event,
        "symbol": symbol,
        "details": json.dumps(
            details or {},
            separators=(",", ":"),
            ensure_ascii=False,
        ),
    }

    write_header = not EVENT_LOG_FILE.exists()

    with EVENT_LOG_FILE.open(
        "a",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "timestamp",
                "event",
                "symbol",
                "details",
            ],
        )

        if write_header:
            writer.writeheader()

        writer.writerow(row)


def save_state():
    payload = {
        "pending_setups": PENDING_SETUPS,
        "last_processed_candle": (
            LAST_PROCESSED_CANDLE
        ),
        "open_positions": OPEN_POSITIONS,
        "last_setup_id": LAST_SETUP_ID,
        "saved_at": utc_now_iso(),
    }

    temp_path = STATE_FILE.with_suffix(
        ".tmp"
    )

    temp_path.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    temp_path.replace(STATE_FILE)


def load_state():
    if not STATE_FILE.exists():
        return

    try:
        payload = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ) as exc:
        log_event(
            "STATE_LOAD_ERROR",
            details={
                "error": str(exc),
            },
        )
        return

    PENDING_SETUPS.update(
        payload.get(
            "pending_setups",
            {},
        )
    )

    LAST_PROCESSED_CANDLE.update(
        payload.get(
            "last_processed_candle",
            {},
        )
    )

    OPEN_POSITIONS.update(
        payload.get(
            "open_positions",
            {},
        )
    )

    LAST_SETUP_ID.update(
        payload.get(
            "last_setup_id",
            {},
        )
    )


def canonical_query(params):
    if not params:
        return ""

    return "".join(
        f"{key}{params[key]}"
        for key in sorted(params)
        if params[key] is not None
    )


def create_signature(
    api_key,
    secret_key,
    timestamp,
    nonce,
    query_params="",
    body="",
):
    if not api_key or not secret_key:
        raise RuntimeError(
            "Bitunix API-Key/Secret fehlen"
        )

    digest_input = (
        nonce
        + timestamp
        + api_key
        + query_params
        + body
    )

    first_hash = hashlib.sha256(
        digest_input.encode(
            "utf-8"
        )
    ).hexdigest()

    return hashlib.sha256(
        (
            first_hash
            + secret_key
        ).encode("utf-8")
    ).hexdigest()


def get_auth_headers(
    params=None,
    body="",
):
    timestamp = str(
        int(time.time() * 1000)
    )
    nonce = uuid.uuid4().hex

    signature = create_signature(
        BITUNIX_API_KEY,
        BITUNIX_SECRET_KEY,
        timestamp,
        nonce,
        query_params=canonical_query(
            params
        ),
        body=body,
    )

    return {
        "api-key": BITUNIX_API_KEY,
        "sign": signature,
        "timestamp": timestamp,
        "nonce": nonce,
        "language": "en-US",
        "Content-Type": (
            "application/json"
        ),
    }


def bitunix_private_request(
    method,
    path,
    params=None,
    payload=None,
):
    body = ""

    if payload is not None:
        body = json.dumps(
            payload,
            separators=(",", ":"),
            ensure_ascii=False,
        )

    response = requests.request(
        method,
        BASE_URL + path,
        params=params,
        data=(
            body
            if body
            else None
        ),
        headers=get_auth_headers(
            params=params,
            body=body,
        ),
        timeout=10,
    )

    response.raise_for_status()

    result = response.json()

    if result.get("code") != 0:
        raise RuntimeError(result)

    return result


def get_klines(
    symbol,
    interval,
    limit=300,
):
    response = requests.get(
        (
            BASE_URL
            + "/api/v1/futures/market/kline"
        ),
        params={
            "symbol": symbol,
            "interval": interval,
            "limit": limit,
        },
        timeout=10,
    )

    response.raise_for_status()

    result = response.json()

    if result.get("code") != 0:
        raise RuntimeError(result)

    candles = []

    for item in result.get(
        "data",
        [],
    ):
        candles.append(
            {
                "timestamp": int(
                    item["time"]
                ),
                "open": float(
                    item["open"]
                ),
                "high": float(
                    item["high"]
                ),
                "low": float(
                    item["low"]
                ),
                "close": float(
                    item["close"]
                ),
                "volume": float(
                    item.get(
                        "baseVol",
                        0,
                    )
                    or 0
                ),
            }
        )

    candles.sort(
        key=lambda candle: (
            candle["timestamp"]
        )
    )

    return candles


def get_pending_positions(
    symbol=None,
):
    params = {}

    if symbol:
        params["symbol"] = symbol

    return bitunix_private_request(
        "GET",
        (
            "/api/v1/futures/position/"
            "get_pending_positions"
        ),
        params=(
            params
            if params
            else None
        ),
    )


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

    return calculate_signal(
        entry_candles,
        confirmation_candles,
        trend_candles,
        swing_lookback,
        sweep_to_bos_candles,
        bos_to_fvg_candles,
        min_fvg_atr_ratio,
    )


def get_last_closed_1m_candle(
    symbol,
):
    candles = get_klines(
        symbol,
        "1m",
        limit=5,
    )

    now_ms = int(
        time.time() * 1000
    )

    closed_candles = [
        candle
        for candle in candles
        if (
            candle["timestamp"]
            + 60_000
            <= now_ms
        )
    ]

    if not closed_candles:
        return None

    return closed_candles[-1]


def pending_entry_reached(
    pending_setup,
    candle,
):
    entry = float(
        pending_setup[
            "setup"
        ]["entry"]
    )

    return (
        float(candle["low"])
        <= entry
        <= float(candle["high"])
    )


def pending_setup_expired(
    pending_setup,
    current_candle_ts,
):
    fvg_timestamp = int(
        pending_setup[
            "setup"
        ]["fvg_timestamp"]
    )

    expiry_candles = int(
        getattr(
            config,
            "SETUP_EXPIRY_CANDLES",
            5,
        )
    )

    return (
        current_candle_ts
        > (
            fvg_timestamp
            + expiry_candles
            * 60_000
        )
    )


def pending_setup_invalidated(
    pending_setup,
    candle,
):
    stop = float(
        pending_setup[
            "setup"
        ]["stop"]
    )

    signal = pending_setup[
        "signal"
    ]

    if signal == "PENDING_LONG":
        return (
            float(
                candle["low"]
            )
            <= stop
        )

    if signal == "PENDING_SHORT":
        return (
            float(
                candle["high"]
            )
            >= stop
        )

    return True


def build_order_plan(
    pending_setup,
    qty=None,
):
    symbol = pending_setup[
        "symbol"
    ]

    setup = pending_setup[
        "setup"
    ]

    signal = pending_setup[
        "signal"
    ]

    if qty is None:
        qty = (
            LIVE_QTY_BY_SYMBOL[
                symbol
            ]
        )

    entry = float(
        setup["entry"]
    )

    stop = float(
        setup["stop"]
    )

    risk = abs(
        entry - stop
    )

    if risk <= 0:
        raise ValueError(
            "Ungültige Risk-Distanz"
        )

    if signal == "PENDING_LONG":
        side = "BUY"
        tp1 = (
            entry + risk
        )
        tp2 = (
            entry
            + risk * 1.2
        )
    elif signal == "PENDING_SHORT":
        side = "SELL"
        tp1 = (
            entry - risk
        )
        tp2 = (
            entry
            - risk * 1.2
        )
    else:
        raise ValueError(
            "Unbekanntes "
            "Pending-Signal"
        )

    return {
        "symbol": symbol,
        "side": side,
        "qty": str(qty),
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "risk_distance": risk,
        "created_at": (
            utc_now_iso()
        ),
    }


def create_approval(plan):
    approval = {
        "status": "WAITING",
        "plan": plan,
        "created_at": (
            utc_now_iso()
        ),
    }

    APPROVAL_FILE.write_text(
        json.dumps(
            approval,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    log_event(
        "APPROVAL_CREATED",
        symbol=plan["symbol"],
        details=plan,
    )

    return approval


def read_approval():
    if not APPROVAL_FILE.exists():
        return None

    try:
        return json.loads(
            APPROVAL_FILE.read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ):
        return None


def clear_approval():
    if APPROVAL_FILE.exists():
        APPROVAL_FILE.unlink()


def approve_pending_order():
    approval = read_approval()

    if (
        not approval
        or approval.get(
            "status"
        )
        != "WAITING"
    ):
        print(
            "Keine wartende "
            "Order vorhanden."
        )
        return

    plan = approval["plan"]

    approval["status"] = (
        "APPROVED"
    )
    approval["approved_at"] = (
        utc_now_iso()
    )

    APPROVAL_FILE.write_text(
        json.dumps(
            approval,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    log_event(
        "APPROVAL_GRANTED",
        symbol=plan["symbol"],
        details=plan,
    )

    print(
        "Order bestätigt:"
    )
    print(
        json.dumps(
            plan,
            indent=2,
            ensure_ascii=False,
        )
    )

    print("")
    print(
        "Die Echtgeld-Ausführung "
        "bleibt getrennt. "
        "Der Bot führt weiterhin "
        "keine autonome Echtgeld-"
        "Order aus."
    )


def reject_pending_order():
    approval = read_approval()

    if (
        not approval
        or approval.get(
            "status"
        )
        != "WAITING"
    ):
        print(
            "Keine wartende "
            "Order vorhanden."
        )
        return

    plan = approval["plan"]

    log_event(
        "APPROVAL_REJECTED",
        symbol=plan["symbol"],
        details=plan,
    )

    clear_approval()

    print(
        "Order abgelehnt."
    )


def show_status():
    print(
        json.dumps(
            {
                "live_trading": (
                    LIVE_TRADING
                ),
                "api_key_loaded": bool(
                    BITUNIX_API_KEY
                ),
                "secret_loaded": bool(
                    BITUNIX_SECRET_KEY
                ),
                "pending_setups": (
                    PENDING_SETUPS
                ),
                "open_positions": (
                    OPEN_POSITIONS
                ),
                "approval": (
                    read_approval()
                ),
            },
            indent=2,
            ensure_ascii=False,
        )
    )


def open_position_from_plan(
    plan,
):
    symbol = plan["symbol"]

    OPEN_POSITIONS[symbol] = {
        "side": plan["side"],
        "qty": plan["qty"],
        "remaining_qty": float(
            plan["qty"]
        ),
        "entry": float(
            plan["entry"]
        ),
        "stop": float(
            plan["stop"]
        ),
        "tp1": float(
            plan["tp1"]
        ),
        "tp2": float(
            plan["tp2"]
        ),
        "tp1_hit": False,
        "tp1_close_percent": float(
            getattr(
                config,
                "TP1_CLOSE_PERCENT",
                70.0,
            )
        ),
        "tp2_close_percent": float(
            getattr(
                config,
                "TP2_CLOSE_PERCENT",
                30.0,
            )
        ),
        "break_even_active": False,
        "status": "OPEN",
        "opened_at": utc_now_iso(),
    }

    save_state()

    log_event(
        "POSITION_OPENED",
        symbol=symbol,
        details=(
            OPEN_POSITIONS[symbol]
        ),
    )

    return OPEN_POSITIONS[
        symbol
    ]


def check_position_levels(
    position,
    candle,
):
    side = position["side"]

    low = float(
        candle["low"]
    )
    high = float(
        candle["high"]
    )

    stop = float(
        position["stop"]
    )
    tp1 = float(
        position["tp1"]
    )
    tp2 = float(
        position["tp2"]
    )

    if side == "BUY":
        if low <= stop:
            return "STOP"

        if high >= tp2:
            return "TP2"

        if (
            not position.get(
                "tp1_hit",
                False,
            )
            and high >= tp1
        ):
            return "TP1"

    if side == "SELL":
        if high >= stop:
            return "STOP"

        if low <= tp2:
            return "TP2"

        if (
            not position.get(
                "tp1_hit",
                False,
            )
            and low <= tp1
        ):
            return "TP1"

    return None


def update_position_state(
    position,
    event,
):
    if event is None:
        return position

    if event == "TP1":
        position["tp1_hit"] = True

        remaining_qty = float(
            position.get(
                "remaining_qty",
                position["qty"],
            )
        )

        close_percent = float(
            position.get(
                "tp1_close_percent",
                70.0,
            )
        )

        closed_qty = (
            remaining_qty
            * close_percent
            / 100.0
        )

        position[
            "remaining_qty"
        ] = max(
            0.0,
            remaining_qty
            - closed_qty,
        )

        position["stop"] = float(
            position["entry"]
        )

        position[
            "break_even_active"
        ] = True

        position["status"] = (
            "OPEN"
        )

        return position

    if event == "TP2":
        position[
            "remaining_qty"
        ] = 0.0

        position["status"] = (
            "CLOSED_TP2"
        )

        return position

    if event == "STOP":
        position[
            "remaining_qty"
        ] = 0.0

        position["status"] = (
            "CLOSED_STOP"
        )

        return position

    return position


def process_open_position(
    symbol,
    candle,
):
    if symbol not in OPEN_POSITIONS:
        return None

    position = OPEN_POSITIONS[
        symbol
    ]

    event = check_position_levels(
        position,
        candle,
    )

    update_position_state(
        position,
        event,
    )

    if event is None:
        return {
            "event": None,
            "position": position,
        }

    log_event(
        f"POSITION_{event}",
        symbol=symbol,
        details=position,
    )

    print("")
    print("POSITION EVENT:")
    print(symbol, event)

    if position["status"].startswith(
        "CLOSED_"
    ):
        closed_position = (
            position.copy()
        )

        del OPEN_POSITIONS[
            symbol
        ]

        save_state()

        return {
            "event": event,
            "position": (
                closed_position
            ),
        }

    save_state()

    return {
        "event": event,
        "position": position,
    }


def store_pending_setup(
    symbol,
    result,
):
    setup = result[
        "indicators"
    ]["setup"]

    setup_id = result[
        "indicators"
    ].get("setup_id")

    if (
        setup_id
        and LAST_SETUP_ID.get(
            symbol
        )
        == setup_id
    ):
        return False

    PENDING_SETUPS[symbol] = {
        "created_at": (
            utc_now_iso()
        ),
        "signal": (
            result["signal"]
        ),
        "setup": setup,
        "setup_id": setup_id,
    }

    if setup_id:
        LAST_SETUP_ID[
            symbol
        ] = setup_id

    save_state()

    log_event(
        "PENDING_CREATED",
        symbol=symbol,
        details=(
            PENDING_SETUPS[
                symbol
            ]
        ),
    )

    return True


def process_pending_setup(
    symbol,
):
    if symbol not in PENDING_SETUPS:
        return

    candle = (
        get_last_closed_1m_candle(
            symbol
        )
    )

    if candle is None:
        return

    candle_ts = candle[
        "timestamp"
    ]

    if (
        LAST_PROCESSED_CANDLE.get(
            symbol
        )
        == candle_ts
    ):
        return

    LAST_PROCESSED_CANDLE[
        symbol
    ] = candle_ts

    pending = PENDING_SETUPS[
        symbol
    ]

    if pending_setup_invalidated(
        pending,
        candle,
    ):
        print(
            "PENDING-SETUP "
            "UNGÜLTIG:",
            symbol,
        )

        log_event(
            "PENDING_INVALIDATED",
            symbol=symbol,
            details=pending,
        )

        del PENDING_SETUPS[
            symbol
        ]

        save_state()
        return

    if pending_setup_expired(
        pending,
        candle_ts,
    ):
        print(
            "PENDING-SETUP "
            "ABGELAUFEN:",
            symbol,
        )

        log_event(
            "PENDING_EXPIRED",
            symbol=symbol,
            details=pending,
        )

        del PENDING_SETUPS[
            symbol
        ]

        save_state()
        return

    if not pending_entry_reached(
        pending,
        candle,
    ):
        save_state()
        return

    plan_input = (
        pending.copy()
    )
    plan_input["symbol"] = (
        symbol
    )

    plan = build_order_plan(
        plan_input
    )

    create_approval(plan)

    print("")
    print(
        "V7 ENTRY ERREICHT – "
        "BESTÄTIGUNG ERFORDERLICH:"
    )

    print(
        json.dumps(
            plan,
            indent=2,
            ensure_ascii=False,
        )
    )

    print("")
    print(
        "Bestätigen mit:"
    )
    print(
        "python live_paper_v7.py "
        "approve"
    )
    print(
        "Ablehnen mit:"
    )
    print(
        "python live_paper_v7.py "
        "reject"
    )

    del PENDING_SETUPS[
        symbol
    ]

    save_state()


def run_loop():
    load_state()

    print(
        "LSOB V7 Live-Paper"
    )
    print(
        "LIVE_TRADING:",
        LIVE_TRADING,
    )
    print(
        "API Key geladen:",
        bool(BITUNIX_API_KEY),
    )
    print(
        "Secret geladen:",
        bool(BITUNIX_SECRET_KEY),
    )

    while True:
        try:
            approval = read_approval()

            if (
                approval
                and approval.get(
                    "status"
                )
                == "WAITING"
            ):
                print("")
                print(
                    "Warte auf "
                    "Order-Bestätigung..."
                )

                print(
                    json.dumps(
                        approval[
                            "plan"
                        ],
                        indent=2,
                        ensure_ascii=False,
                    )
                )

                time.sleep(
                    POLL_SECONDS
                )
                continue

            for symbol in SYMBOLS:
                if (
                    symbol
                    in OPEN_POSITIONS
                ):
                    position_candle = (
                        get_last_closed_1m_candle(
                            symbol
                        )
                    )

                    if (
                        position_candle
                        is not None
                    ):
                        process_open_position(
                            symbol,
                            position_candle,
                        )

                process_pending_setup(
                    symbol
                )

                result = analyze_symbol(
                    symbol
                )

                if (
                    result
                    and str(
                        result.get(
                            "signal",
                            "",
                        )
                    ).startswith(
                        "PENDING_"
                    )
                    and symbol
                    not in PENDING_SETUPS
                    and symbol
                    not in OPEN_POSITIONS
                ):
                    if store_pending_setup(
                        symbol,
                        result,
                    ):
                        print("")
                        print(
                            "NEUES "
                            "PENDING-SETUP "
                            "GESPEICHERT:"
                        )
                        print(symbol)
                        print(
                            json.dumps(
                                PENDING_SETUPS[
                                    symbol
                                ],
                                indent=2,
                                ensure_ascii=False,
                            )
                        )

                print("")
                print(symbol)
                print(
                    "Signal:",
                    (
                        None
                        if result
                        is None
                        else result.get(
                            "signal"
                        )
                    ),
                )
                print(
                    "Grund:",
                    (
                        None
                        if result
                        is None
                        else result.get(
                            "reason"
                        )
                    ),
                )

            print("")
            print(
                "Warte",
                POLL_SECONDS,
                "Sekunden..."
            )

            time.sleep(
                POLL_SECONDS
            )

        except KeyboardInterrupt:
            save_state()
            print("")
            print("Bot beendet.")
            break

        except Exception as exc:
            log_event(
                "LOOP_ERROR",
                details={
                    "error": str(exc),
                },
            )
            print(
                "Fehler:",
                exc,
            )
            time.sleep(
                POLL_SECONDS
            )


def main():
    load_state()

    command = (
        sys.argv[1].lower()
        if len(sys.argv) > 1
        else "run"
    )

    if command == "run":
        run_loop()

    elif command == "approve":
        approve_pending_order()

    elif command == "reject":
        reject_pending_order()

    elif command == "status":
        show_status()

    elif command == "positions":
        if (
            not BITUNIX_API_KEY
            or not BITUNIX_SECRET_KEY
        ):
            print(
                "API-Key/Secret fehlen."
            )
            return

        print(
            json.dumps(
                get_pending_positions(),
                indent=2,
                ensure_ascii=False,
            )
        )

    else:
        print(
            "Unbekannter Befehl:",
            command,
        )
        print(
            "Verfügbar: "
            "run, approve, reject, "
            "status, positions"
        )


if __name__ == "__main__":
    main()



def main():
    load_current_state()

    command = (
        sys.argv[1].lower()
        if len(sys.argv) > 1
        else "run"
    )

    if command == "run":
        run_loop()
        return

    if command == "approve":
        approval = approve_order()
        if approval is None:
            print("Keine wartende Order vorhanden.")
            return

        print("Orderplan bestätigt:")
        print(
            json.dumps(
                approval["plan"],
                indent=2,
                ensure_ascii=False,
            )
        )
        print("")
        print(
            "Die Echtgeld-Ausführung bleibt getrennt "
            "und wird nicht autonom ausgelöst."
        )
        return

    if command == "reject":
        approval = reject_order()
        if approval is None:
            print("Keine wartende Order vorhanden.")
            return

        print("Orderplan abgelehnt.")
        return

    if command == "status":
        print(
            json.dumps(
                {
                    "pending_setups": PENDING_SETUPS,
                    "open_positions": OPEN_POSITIONS,
                    "approval": read_approval(),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    print("Unbekannter Befehl:", command)
    print("Verfügbar: run, approve, reject, status")


if __name__ == "__main__":
    main()
