import json
from datetime import datetime, timezone
from pathlib import Path

APPROVAL_FILE = Path("v8_ada_pending_approval.json")


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def read_v8_ada_approval():
    if not APPROVAL_FILE.exists():
        return None
    try:
        return json.loads(APPROVAL_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def create_v8_ada_approval(plan):
    approval = {
        "status": "WAITING",
        "created_at": utc_now_iso(),
        "plan": plan,
    }
    APPROVAL_FILE.write_text(
        json.dumps(approval, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return approval


def approve_v8_ada_order():
    approval = read_v8_ada_approval()
    if not approval or approval.get("status") != "WAITING":
        return None
    approval["status"] = "APPROVED"
    approval["approved_at"] = utc_now_iso()
    APPROVAL_FILE.write_text(
        json.dumps(approval, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return approval


def reject_v8_ada_order():
    approval = read_v8_ada_approval()
    if not approval or approval.get("status") != "WAITING":
        return None
    approval["status"] = "REJECTED"
    approval["rejected_at"] = utc_now_iso()
    APPROVAL_FILE.write_text(
        json.dumps(approval, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return approval


def clear_v8_ada_approval():
    if APPROVAL_FILE.exists():
        APPROVAL_FILE.unlink()
