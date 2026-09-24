from datetime import datetime
from decimal import Decimal
from pathlib import Path
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo
from unittest.mock import patch

import two_hour_status as status


TZ = ZoneInfo("Europe/Vienna")


def main():
    now = datetime(
        2026,
        9,
        21,
        10,
        0,
        tzinfo=TZ,
    )

    last_two_hours = {
        "closed": [],
        "open": [
            {
                "symbol": "BTCUSDT",
                "side": "LONG",
                "qty": "0.0001",
                "unrealized": Decimal("0.12"),
            }
        ],
        "realized": Decimal("0"),
        "fees": Decimal("0.01"),
        "funding": Decimal("0"),
        "net": Decimal("0"),
    }

    today = {
        "closed": [
            {"net": Decimal("0.10")},
            {"net": Decimal("-0.05")},
        ],
        "open": [],
        "realized": Decimal("0.08"),
        "fees": Decimal("0.03"),
        "funding": Decimal("0"),
        "net": Decimal("0.05"),
    }

    total = {
        "closed": [
            {"net": Decimal("0.10")},
            {"net": Decimal("-0.05")},
            {"net": Decimal("0.20")},
            {"net": Decimal("0.15")},
        ],
        "open": [],
        "realized": Decimal("0.48"),
        "fees": Decimal("0.08"),
        "funding": Decimal("0"),
        "net": Decimal("0.40"),
    }

    with patch.object(
        status,
        "live_summary",
        side_effect=[
            last_two_hours,
            today,
            total,
        ],
    ), patch.object(
        status,
        "v8_summary",
        side_effect=[{
            "entries": 1,
            "tp1": 0,
            "closed": 0,
            "net": Decimal("0"),
            "wins": 0,
            "losses": 0,
            "position_open": True,
        }, {
            "entries": 2,
            "tp1": 1,
            "closed": 1,
            "net": Decimal("0.15"),
            "wins": 1,
            "losses": 0,
            "position_open": False,
        }],
    ), patch.object(
        status,
        "auto_live_status",
        return_value={"active": True},
    ), patch.object(
        status,
        "tracking_start",
        return_value=datetime(
            2026,
            9,
            20,
            18,
            0,
            tzinfo=TZ,
        ),
    ):
        message = status.build_message(now)

    assert "LSOB 2-STUNDEN-UPDATE" in message
    assert "Auto-Live: AKTIV" in message
    assert "BTCUSDT LONG" in message
    assert "Entries letzte 2h: 1" in message
    assert "Paper-Position offen: JA" in message
    assert "V8 ADA EXPIRY2 + FVG015 PAPER" in message
    assert "Entries letzte 2h: 2" in message
    assert "Paper-Netto letzte 2h: +0.1500 USDT" in message
    assert "Paper-Position offen: NEIN" in message
    assert "Trades heute: 2" in message
    assert "Trefferquote heute: 50.0%" in message
    assert "Trades gesamt: 4" in message
    assert "Trefferquote gesamt: 75.0%" in message
    assert "Gesamt-Netto: +0.4000 USDT" in message

    with TemporaryDirectory() as folder:
        events = Path(folder) / "events.jsonl"
        state_file = Path(folder) / "state.json"
        events.write_text(
            '{"ts": 1789977600, "event": "ENTRY", "details": {}}\n'
            '{"ts": 1789977600, "event": "EXIT", "details": {"net": 0.12}}\n',
            encoding="utf-8",
        )
        state_file.write_text('{"position": null}', encoding="utf-8")
        with patch.object(status, "V12_EVENT_FILE", events), patch.object(
            status, "V12_STATE_FILE", state_file
        ):
            result = status.v12_summary(
                datetime(2026, 9, 21, 0, 0, tzinfo=TZ),
                datetime(2026, 9, 22, 0, 0, tzinfo=TZ),
            )
        assert result["enabled"] and result["entries"] == 1
        assert result["closed"] == 1 and result["net"] == Decimal("0.12")

    print("2-STUNDEN-UPDATE SELFTEST ERFOLGREICH")
    print("Keine echte Order wurde gesendet.")


if __name__ == "__main__":
    main()
