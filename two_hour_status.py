from datetime import datetime, timedelta

from live_auto_mode import auto_live_status
from morning_summary import (
    TZ,
    fmt_money,
    live_summary,
    v8_summary,
)
from telegram_approval import (
    send_message,
    telegram_configured,
)


def build_message(now=None):
    end = now or datetime.now(TZ)
    start = end - timedelta(hours=2)

    live = live_summary(start, end)
    v8 = v8_summary(start, end)
    auto = auto_live_status()

    lines = [
        "LSOB 2-STUNDEN-UPDATE",
        (
            f"{start.strftime('%d.%m. %H:%M')}–"
            f"{end.strftime('%H:%M')} "
            "Europe/Vienna"
        ),
        "",
        "V7 ECHTGELD",
        f"Auto-Live: {'AKTIV' if auto.get('active') else 'AUS'}",
        f"Geschlossene Positionen: {len(live['closed'])}",
        f"Netto letzte 2h: {fmt_money(live['net'])}",
        f"Gebühren: {live['fees']:.4f} USDT",
        f"Funding: {live['funding']:+.4f} USDT",
        f"Offene Positionen: {len(live['open'])}",
    ]

    for item in live["open"][:4]:
        lines.append(
            f"{item['symbol']} {item['side']} "
            f"Qty {item['qty']} | unreal. "
            f"{fmt_money(item['unrealized'])}"
        )

    lines.extend(
        [
            "",
            "V8 BTC BALANCED PAPER",
            f"Entries letzte 2h: {v8['entries']}",
            f"TP1 letzte 2h: {v8['tp1']}",
            f"Geschlossene Trades: {v8['closed']}",
            f"Paper-Netto letzte 2h: {fmt_money(v8['net'])}",
            (
                "Paper-Position offen: "
                + ("JA" if v8["position_open"] else "NEIN")
            ),
        ]
    )

    return "\n".join(lines)


def main():
    message = build_message()
    print(message)

    if telegram_configured():
        send_message(message)
        print("")
        print("2-STUNDEN-UPDATE AN TELEGRAM GESENDET")
    else:
        print("")
        print("Telegram nicht konfiguriert.")


if __name__ == "__main__":
    main()
