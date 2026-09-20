import csv
import json
from datetime import datetime, timezone
from pathlib import Path


STATE_FILE = Path("runtime_state.json")
APPROVAL_FILE = Path("pending_approval.json")
EVENT_LOG_FILE = Path("trade_events.csv")


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def save_runtime_state(
    pending_setups,
    last_processed_candle,
    open_positions,
    last_setup_id=None,
):
    payload = {
        "pending_setups": pending_setups,
        "last_processed_candle": last_processed_candle,
        "open_positions": open_positions,
        "last_setup_id": last_setup_id or {},
        "saved_at": utc_now_iso(),
    }

    temp_file = STATE_FILE.with_suffix(".tmp")
    temp_file.write_text(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temp_file.replace(STATE_FILE)


def load_runtime_state():
    if not STATE_FILE.exists():
        return {
            "pending_setups": {},
            "last_processed_candle": {},
            "open_positions": {},
            "last_setup_id": {},
        }

    try:
        payload = json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ):
        return {
            "pending_setups": {},
            "last_processed_candle": {},
            "open_positions": {},
            "last_setup_id": {},
        }

    return {
        "pending_setups": payload.get(
            "pending_setups",
            {},
        ),
        "last_processed_candle": payload.get(
            "last_processed_candle",
            {},
        ),
        "open_positions": payload.get(
            "open_positions",
            {},
        ),
        "last_setup_id": payload.get(
            "last_setup_id",
            {},
        ),
    }


def log_event(
    event,
    symbol="",
    details=None,
):
    write_header = not EVENT_LOG_FILE.exists()

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


def create_approval(plan):
    approval = {
        "status": "WAITING",
        "created_at": utc_now_iso(),
        "plan": plan,
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
        symbol=plan.get("symbol", ""),
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


def approve_order():
    approval = read_approval()

    if (
        not approval
        or approval.get("status") != "WAITING"
    ):
        return None

    approval["status"] = "APPROVED"
    approval["approved_at"] = utc_now_iso()

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
        symbol=approval["plan"].get(
            "symbol",
            "",
        ),
        details=approval["plan"],
    )

    return approval


def reject_order():
    approval = read_approval()

    if (
        not approval
        or approval.get("status") != "WAITING"
    ):
        return None

    approval["status"] = "REJECTED"
    approval["rejected_at"] = utc_now_iso()

    APPROVAL_FILE.write_text(
        json.dumps(
            approval,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    log_event(
        "APPROVAL_REJECTED",
        symbol=approval["plan"].get(
            "symbol",
            "",
        ),
        details=approval["plan"],
    )

    return approval
