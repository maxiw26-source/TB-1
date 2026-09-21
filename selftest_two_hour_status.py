from datetime import datetime
from decimal import Decimal
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

    with patch.object(
        status,
        "live_summary",
        return_value={
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
        },
    ), patch.object(
        status,
        "v8_summary",
        return_value={
            "entries": 1,
            "tp1": 0,
            "closed": 0,
            "net": Decimal("0"),
            "wins": 0,
            "losses": 0,
            "position_open": True,
        },
    ), patch.object(
        status,
        "auto_live_status",
        return_value={"active": True},
    ):
        message = status.build_message(now)

    assert "LSOB 2-STUNDEN-UPDATE" in message
    assert "Auto-Live: AKTIV" in message
    assert "BTCUSDT LONG" in message
    assert "Entries letzte 2h: 1" in message
    assert "Paper-Position offen: JA" in message

    print("2-STUNDEN-UPDATE SELFTEST ERFOLGREICH")
    print("Keine echte Order wurde gesendet.")


if __name__ == "__main__":
    main()
