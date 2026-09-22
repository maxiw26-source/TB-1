import json
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path

from live_auto_mode import auto_live_status
from morning_summary import (
    TZ,
    fmt_money,
    live_summary,
    v8_summary,
)
EXECUTION_STATE_FILE = Path("live_execution_state.json")
ADA_EVENT_FILE = Path("v8_ada_paper_events.jsonl")
ADA_STATE_FILE = Path("v8_ada_paper_state.json")


from telegram_approval import (
    send_message,
    telegram_configured,
)



def tracking_start(now):
    if not EXECUTION_STATE_FILE.exists():
        return now

    try:
        payload = json.loads(
            EXECUTION_STATE_FILE.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return now

    timestamps = []

    for attempt in (
        payload.get("attempts", {})
        or {}
    ).values():
        raw = attempt.get("updated_at")

        if not raw:
            continue

        try:
            parsed = datetime.fromisoformat(
                str(raw).replace(
                    "Z",
                    "+00:00",
                )
            ).astimezone(TZ)
        except Exception:
            continue

        timestamps.append(parsed)

    return min(timestamps) if timestamps else now


def hit_rate(closed):
    if not closed:
        return 0.0

    winners = sum(
        1
        for item in closed
        if item["net"] > 0
    )

    return (
        100.0
        * winners
        / len(closed)
    )


def build_message(now=None):
    end = now or datetime.now(TZ)
    start = end - timedelta(hours=2)

    live = live_summary(start, end)
    v8 = v8_summary(start, end)
    ada = v8_summary(
        start, end,
        event_file=ADA_EVENT_FILE,
        state_file=ADA_STATE_FILE,
    )
    auto = auto_live_status()

    day_start = datetime.combine(
        end.date(),
        dt_time.min,
        TZ,
    )
    live_today = live_summary(
        day_start,
        end,
    )

    since_start = tracking_start(end)
    live_total = live_summary(
        since_start,
        end,
    )

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
        "",
        "V7 HEUTE",
        f"Trades heute: {len(live_today['closed'])}",
        f"Trefferquote heute: {hit_rate(live_today['closed']):.1f}%",
        f"Netto heute: {fmt_money(live_today['net'])}",
        "",
        "V7 SEIT BOT-TRACKING",
        (
            "Seit: "
            + since_start.strftime("%d.%m.%Y %H:%M")
        ),
        f"Trades gesamt: {len(live_total['closed'])}",
        f"Trefferquote gesamt: {hit_rate(live_total['closed']):.1f}%",
        f"Gesamt-Netto: {fmt_money(live_total['net'])}",
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
            "",
            "V8 ADA EXPIRY2 + FVG015 PAPER",
            f"Entries letzte 2h: {ada['entries']}",
            f"TP1 letzte 2h: {ada['tp1']}",
            f"Geschlossene Trades: {ada['closed']}",
            f"Gewinner/Verlierer: {ada['wins']}/{ada['losses']}",
            f"Paper-Netto letzte 2h: {fmt_money(ada['net'])}",
            (
                "Paper-Position offen: "
                + ("JA" if ada["position_open"] else "NEIN")
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
