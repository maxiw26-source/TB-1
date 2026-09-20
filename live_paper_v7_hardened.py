import json
import os
import sys
import time

import requests

import config
from bot_runtime import (
    approve_order,
    clear_approval,
    create_approval,
    load_runtime_state,
    log_event,
    read_approval,
    reject_order,
    save_runtime_state,
)
from strategy_v7 import calculate_signal
from telegram_approval import (
    notify_plan,
    process_updates,
    telegram_configured,
)


BASE_URL = "https://fapi.bitunix.com"

SYMBOLS = [
    "BTCUSDT",
    "ETHUSDT",
]

POLL_SECONDS = 30

LIVE_QTY_BY_SYMBOL = {
    "BTCUSDT": "0.0001",
    "ETHUSDT": "0.003",
}

PENDING_SETUPS = {}
LAST_PROCESSED_CANDLE = {}
OPEN_POSITIONS = {}
LAST_SETUP_ID = {}


def load_env_file(path=".env"):
    if not os.path.exists(path):
        return

    with open(
        path,
        "r",
        encoding="utf-8",
    ) as handle:
        for raw_line in handle:
            line = raw_line.strip()

            if (
                not line
                or line.startswith("#")
                or "=" not in line
            ):
                continue

            key, value = line.split(
                "=",
                1,
            )

            key = key.strip()
            value = (
                value.strip()
                .strip('"')
                .strip("'")
            )

            if (
                key
                and key not in os.environ
            ):
                os.environ[key] = value


load_env_file()


def save_state():
    save_runtime_state(
        PENDING_SETUPS,
        LAST_PROCESSED_CANDLE,
        OPEN_POSITIONS,
        LAST_SETUP_ID,
    )


def load_state():
    state = load_runtime_state()

    PENDING_SETUPS.clear()
    PENDING_SETUPS.update(
        state.get(
            "pending_setups",
            {},
        )
    )

    LAST_PROCESSED_CANDLE.clear()
    LAST_PROCESSED_CANDLE.update(
        state.get(
            "last_processed_candle",
            {},
        )
    )

    OPEN_POSITIONS.clear()
    OPEN_POSITIONS.update(
        state.get(
            "open_positions",
            {},
        )
    )

    LAST_SETUP_ID.clear()
    LAST_SETUP_ID.update(
        state.get(
            "last_setup_id",
            {},
        )
    )


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

    closed = [
        candle
        for candle in candles
        if (
            candle["timestamp"]
            + 60_000
            <= now_ms
        )
    ]

    if not closed:
        return None

    return closed[-1]


def pending_entry_reached(
    pending,
    candle,
):
    entry = float(
        pending["setup"]["entry"]
    )

    return (
        float(candle["low"])
        <= entry
        <= float(candle["high"])
    )


def pending_setup_invalidated(
    pending,
    candle,
):
    stop = float(
        pending["setup"]["stop"]
    )

    signal = pending["signal"]

    if signal == "PENDING_LONG":
        return (
            float(candle["low"])
            <= stop
        )

    if signal == "PENDING_SHORT":
        return (
            float(candle["high"])
            >= stop
        )

    return True


def pending_setup_expired(
    pending,
    candle_ts,
):
    fvg_timestamp = int(
        pending[
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
        candle_ts
        > (
            fvg_timestamp
            + expiry_candles
            * 60_000
        )
    )


def build_order_plan(
    symbol,
    pending,
):
    setup = pending["setup"]
    signal = pending["signal"]

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
            "Unbekanntes Pending-Signal"
        )

    return {
        "symbol": symbol,
        "side": side,
        "qty": (
            LIVE_QTY_BY_SYMBOL[
                symbol
            ]
        ),
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "risk_distance": risk,
    }


def open_position_from_plan(plan):
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
        "break_even_active": False,
        "status": "OPEN",
    }

    save_state()

    log_event(
        "POSITION_TRACKING_STARTED",
        symbol=symbol,
        details=(
            OPEN_POSITIONS[
                symbol
            ]
        ),
    )


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
        return

    if event == "TP1":
        position["tp1_hit"] = True

        remaining_qty = float(
            position[
                "remaining_qty"
            ]
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
        return

    if event == "TP2":
        position[
            "remaining_qty"
        ] = 0.0
        position["status"] = (
            "CLOSED_TP2"
        )
        return

    if event == "STOP":
        position[
            "remaining_qty"
        ] = 0.0
        position["status"] = (
            "CLOSED_STOP"
        )


def process_open_position(
    symbol,
):
    if symbol not in OPEN_POSITIONS:
        return

    candle = (
        get_last_closed_1m_candle(
            symbol
        )
    )

    if candle is None:
        return

    position = OPEN_POSITIONS[
        symbol
    ]

    event = check_position_levels(
        position,
        candle,
    )

    if event is None:
        return

    update_position_state(
        position,
        event,
    )

    log_event(
        f"POSITION_{event}",
        symbol=symbol,
        details=position,
    )

    print(
        "POSITION EVENT:",
        symbol,
        event,
    )

    if position[
        "status"
    ].startswith("CLOSED_"):
        del OPEN_POSITIONS[
            symbol
        ]

    save_state()


def store_pending_setup(
    symbol,
    result,
):
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
        "signal": result[
            "signal"
        ],
        "setup": result[
            "indicators"
        ]["setup"],
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

    plan = build_order_plan(
        symbol,
        pending,
    )

    create_approval(plan)

    if telegram_configured():
        try:
            notify_plan(plan)
            log_event(
                "TELEGRAM_PLAN_SENT",
                symbol=symbol,
                details=plan,
            )
        except Exception as exc:
            log_event(
                "TELEGRAM_NOTIFY_ERROR",
                symbol=symbol,
                details={
                    "error": str(exc),
                },
            )

    print("")
    print(
        "ENTRY ERREICHT – "
        "BESTÄTIGUNG ERFORDERLICH"
    )
    print(
        json.dumps(
            plan,
            indent=2,
            ensure_ascii=False,
        )
    )

    del PENDING_SETUPS[
        symbol
    ]

    save_state()


def run_loop():
    load_state()

    print(
        "LSOB V7 läuft."
    )
    print(
        "Alle echten Aktionen "
        "bleiben bestätigungspflichtig."
    )
    print(
        "Telegram:",
        (
            "aktiv"
            if telegram_configured()
            else "nicht eingerichtet"
        ),
    )

    while True:
        try:
            if telegram_configured():
                try:
                    actions = process_updates()

                    for action in actions:
                        log_event(
                            "TELEGRAM_ACTION",
                            details={
                                "action": action,
                            },
                        )
                except Exception as exc:
                    log_event(
                        "TELEGRAM_POLL_ERROR",
                        details={
                            "error": str(exc),
                        },
                    )

            approval = read_approval()

            if (
                approval
                and approval.get(
                    "status"
                )
                == "APPROVED"
            ):
                plan = approval["plan"]

                if plan.get("test"):
                    log_event(
                        "TEST_APPROVAL_CONSUMED",
                        symbol=plan.get(
                            "symbol",
                            "",
                        ),
                        details=plan,
                    )
                    clear_approval()
                    approval = None

                    print(
                        "Test-Bestätigung "
                        "erfolgreich verarbeitet."
                    )
                else:
                    if (
                        plan["symbol"]
                        not in OPEN_POSITIONS
                    ):
                        open_position_from_plan(
                            plan
                        )

                    clear_approval()
                    approval = None

                    print(
                        "Bestätigter Plan "
                        "wird jetzt überwacht."
                    )

            if (
                approval
                and approval.get(
                    "status"
                )
                == "REJECTED"
            ):
                clear_approval()
                approval = None

            if (
                approval
                and approval.get(
                    "status"
                )
                == "WAITING"
            ):
                print(
                    "Warte auf Bestätigung."
                )
                print(
                    "python "
                    "live_paper_v7_hardened.py "
                    "approve"
                )
                print(
                    "oder:"
                )
                print(
                    "python "
                    "live_paper_v7_hardened.py "
                    "reject"
                )

                time.sleep(
                    POLL_SECONDS
                )
                continue

            for symbol in SYMBOLS:
                process_open_position(
                    symbol
                )

                process_pending_setup(
                    symbol
                )

                result = analyze_symbol(
                    symbol
                )

                signal = (
                    None
                    if result is None
                    else result.get(
                        "signal"
                    )
                )

                if (
                    str(signal).startswith(
                        "PENDING_"
                    )
                    and symbol
                    not in PENDING_SETUPS
                    and symbol
                    not in OPEN_POSITIONS
                ):
                    store_pending_setup(
                        symbol,
                        result,
                    )

                print(
                    symbol,
                    "Signal:",
                    signal,
                    "|",
                    (
                        None
                        if result is None
                        else result.get(
                            "reason"
                        )
                    ),
                )

            time.sleep(
                POLL_SECONDS
            )

        except KeyboardInterrupt:
            save_state()
            print(
                "Bot beendet."
            )
            return

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


def approve_command():
    approval = approve_order()

    if approval is None:
        print(
            "Keine wartende "
            "Bestätigung vorhanden."
        )
        return

    plan = approval["plan"]

    print(
        "Plan bestätigt:"
    )
    print(
        json.dumps(
            plan,
            indent=2,
            ensure_ascii=False,
        )
    )
    print(
        "Der laufende Bot übernimmt "
        "die interne Überwachung "
        "innerhalb des nächsten "
        "Prüfintervalls."
    )
    print(
        "Es wurde keine autonome "
        "Echtgeld-Order gesendet."
    )


def reject_command():
    approval = reject_order()

    if approval is None:
        print(
            "Keine wartende "
            "Bestätigung vorhanden."
        )
        return

    print(
        "Plan abgelehnt."
    )


def status_command():
    load_state()

    print(
        json.dumps(
            {
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


def test_approval_command():
    plan = {
        "symbol": "BTCUSDT",
        "side": "BUY",
        "qty": "0.0001",
        "entry": 100.0,
        "stop": 95.0,
        "tp1": 105.0,
        "tp2": 106.0,
        "risk_distance": 5.0,
        "test": True,
    }

    create_approval(plan)

    if telegram_configured():
        notify_plan(plan)

    print(
        "Test-Bestätigung erstellt."
    )
    print(
        "Antworte in Telegram mit "
        "/approve oder /reject."
    )


def main():
    command = (
        sys.argv[1].lower()
        if len(sys.argv) > 1
        else "run"
    )

    if command == "run":
        run_loop()
        return

    if command == "approve":
        approve_command()
        return

    if command == "reject":
        reject_command()
        return

    if command == "status":
        status_command()
        return

    if command == "test-approval":
        test_approval_command()
        return

    print(
        "Verfügbar: "
        "run, approve, reject, status, "
        "test-approval"
    )


if __name__ == "__main__":
    main()
