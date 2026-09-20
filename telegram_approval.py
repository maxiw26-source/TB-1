import json
import os
from pathlib import Path

import requests

from bot_runtime import (
    approve_order,
    read_approval,
    reject_order,
)
from bitunix_live import live_execution_status


STATE_FILE = Path("telegram_state.json")


def telegram_configured():
    return bool(
        os.getenv("TELEGRAM_BOT_TOKEN")
        and os.getenv("TELEGRAM_CHAT_ID")
    )


def _token():
    return os.getenv(
        "TELEGRAM_BOT_TOKEN"
    )


def _chat_id():
    return str(
        os.getenv(
            "TELEGRAM_CHAT_ID",
            "",
        )
    )


def _api_url(method):
    return (
        "https://api.telegram.org/bot"
        + _token()
        + "/"
        + method
    )


def _load_offset():
    if not STATE_FILE.exists():
        return 0

    try:
        payload = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )
        return int(
            payload.get(
                "last_update_id",
                0,
            )
        )
    except Exception:
        return 0


def _save_offset(update_id):
    STATE_FILE.write_text(
        json.dumps(
            {
                "last_update_id": int(
                    update_id
                )
            }
        ),
        encoding="utf-8",
    )


def send_message(text):
    if not telegram_configured():
        return False

    response = requests.post(
        _api_url("sendMessage"),
        json={
            "chat_id": _chat_id(),
            "text": text,
        },
        timeout=10,
    )

    response.raise_for_status()

    payload = response.json()

    if not payload.get("ok"):
        raise RuntimeError(payload)

    return True


def format_plan(plan):
    return (
        "LSOB V7 – Bestätigung erforderlich\n"
        f"Symbol: {plan['symbol']}\n"
        f"Seite: {plan['side']}\n"
        f"Menge: {plan['qty']}\n"
        f"Entry: {plan['entry']}\n"
        f"Stop: {plan['stop']}\n"
        f"TP1: {plan['tp1']}\n"
        f"TP2: {plan['tp2']}\n\n"
        "Antworten mit:\n"
        "/approve\n"
        "/reject\n"
        "/status"
    )


def notify_plan(plan):
    return send_message(
        format_plan(plan)
    )


def _status_text():
    approval = read_approval()
    live_status = (
        live_execution_status()
    )

    live_label = "AUS"

    if live_status.get("enabled"):
        live_label = (
            "ARMED"
            if live_status.get(
                "armed_ack"
            )
            else "EIN, ABER NICHT ARMED"
        )

    if not approval:
        return (
            "LSOB V7 Status\n"
            "Keine wartende Bestätigung.\n"
            f"Live-Ausführung: {live_label}"
        )

    status = approval.get(
        "status",
        "UNKNOWN",
    )

    plan = approval.get(
        "plan",
        {},
    )

    symbol = plan.get(
        "symbol",
        "-",
    )

    return (
        "LSOB V7 Status\n"
        f"Approval: {status}\n"
        f"Symbol: {symbol}\n"
        f"Live-Ausführung: {live_label}"
    )


def process_updates():
    if not telegram_configured():
        return []

    last_update_id = _load_offset()

    response = requests.get(
        _api_url("getUpdates"),
        params={
            "offset": (
                last_update_id + 1
            ),
            "timeout": 0,
            "limit": 20,
        },
        timeout=10,
    )

    response.raise_for_status()

    payload = response.json()

    if not payload.get("ok"):
        raise RuntimeError(payload)

    actions = []

    for update in payload.get(
        "result",
        [],
    ):
        update_id = int(
            update["update_id"]
        )

        _save_offset(
            update_id
        )

        message = update.get(
            "message"
        ) or {}

        chat = message.get(
            "chat"
        ) or {}

        if str(
            chat.get("id", "")
        ) != _chat_id():
            continue

        text = str(
            message.get(
                "text",
                "",
            )
        ).strip().lower()

        if text == "/approve":
            approval = approve_order()

            if approval is None:
                send_message(
                    "Keine wartende "
                    "Bestätigung vorhanden."
                )
            else:
                send_message(
                    "Plan bestätigt. "
                    "Der laufende Bot "
                    "übernimmt die interne "
                    "Überwachung."
                )
                actions.append(
                    "APPROVED"
                )

        elif text == "/reject":
            approval = reject_order()

            if approval is None:
                send_message(
                    "Keine wartende "
                    "Bestätigung vorhanden."
                )
            else:
                send_message(
                    "Plan abgelehnt."
                )
                actions.append(
                    "REJECTED"
                )

        elif text == "/status":
            send_message(
                _status_text()
            )
            actions.append(
                "STATUS"
            )

    return actions
