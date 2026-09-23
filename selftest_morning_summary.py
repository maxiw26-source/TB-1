from datetime import datetime
from zoneinfo import ZoneInfo
from unittest.mock import patch

import morning_summary as summary


TZ = ZoneInfo("Europe/Vienna")


def main():
    now = datetime(
        2026,
        9,
        21,
        7,
        15,
        tzinfo=TZ,
    )

    with patch.object(
        summary,
        "get_history_positions",
        side_effect=lambda symbol: [
            {
                "symbol": symbol,
                "side": "LONG",
                "entryPrice": "100",
                "closePrice": "101",
                "realizedPNL": "0.20",
                "fee": "0.03",
                "funding": "-0.01",
                "mtime": int(
                    datetime(
                        2026,
                        9,
                        21,
                        1,
                        0,
                        tzinfo=TZ,
                    ).timestamp()
                    * 1000
                ),
            }
        ],
    ), patch.object(
        summary,
        "get_pending_positions",
        return_value=[],
    ), patch.object(
        summary,
        "v8_summary",
        side_effect=[
            {
                "entries": 1,
                "tp1": 1,
                "closed": 1,
                "net": summary.Decimal("0.12"),
                "wins": 1,
                "losses": 0,
                "position_open": False,
            },
            {
                "entries": 3,
                "tp1": 2,
                "closed": 2,
                "net": summary.Decimal("0.34"),
                "wins": 1,
                "losses": 1,
                "position_open": True,
            },
        ],
    ), patch.object(
        summary,
        "auto_live_status",
        return_value={
            "active": True,
        },
    ):
        message = summary.build_message(now)

    assert "LSOB MORGENBERICHT" in message
    assert "Auto-Live: AKTIV" in message
    assert "Geschlossene Positionen: 2" in message
    assert "Gebühren: 0.0600 USDT" in message
    assert "Netto: +0.3200 USDT" in message
    assert "V8 BTC BALANCED PAPER" in message
    assert "V8 ADA EXPIRY2 + FVG015 PAPER" in message
    assert "Entries: 3" in message
    assert "Trefferquote: 50.0%" in message
    assert "Paper-Netto: +0.3400 USDT" in message
    assert "Paper-Position offen: JA" in message

    print("MORGENBERICHT SELFTEST ERFOLGREICH")
    print("Keine echte Order wurde gesendet.")


if __name__ == "__main__":
    main()
