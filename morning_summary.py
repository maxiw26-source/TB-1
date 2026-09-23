import json
import os
from datetime import datetime, time as dt_time, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from bitunix_live import (
    get_pending_positions,
    private_request,
)
from live_auto_mode import auto_live_status
from telegram_approval import (
    send_message,
    telegram_configured,
)


TZ = ZoneInfo("Europe/Vienna")
SYMBOLS = ["BTCUSDT", "ETHUSDT"]
V8_EVENT_FILE = Path("v8_paper_events.jsonl")
V8_STATE_FILE = Path("v8_paper_state.json")
ADA_EVENT_FILE = Path("v8_ada_paper_events.jsonl")
ADA_STATE_FILE = Path("v8_ada_paper_state.json")


def load_env_file(path=".env"):
    if not os.path.exists(path):
        return

    with open(path, "r", encoding="utf-8") as handle:
        for raw_line in handle:
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


def dec(value):
    try:
        return Decimal(str(value or "0"))
    except Exception:
        return Decimal("0")


def overnight_window(now=None):
    current = now or datetime.now(TZ)
    today = current.date()

    end = datetime.combine(
        today,
        dt_time(hour=7, minute=15),
        TZ,
    )

    if current < end:
        end = current

    start = datetime.combine(
        end.date() - timedelta(days=1),
        dt_time(hour=20, minute=0),
        TZ,
    )

    return start, end


def get_history_positions(symbol):
    result = private_request(
        "GET",
        "/api/v1/futures/position/get_history_positions",
        params={
            "symbol": symbol,
            "skip": 0,
            "limit": 100,
        },
    )

    data = result.get("data") or {}

    if isinstance(data, dict):
        return data.get("positionList", []) or []

    return []


def live_summary(start, end):
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)

    closed = []

    for symbol in SYMBOLS:
        for item in get_history_positions(symbol):
            modified = int(item.get("mtime") or 0)

            if not (
                start_ms
                <= modified
                <= end_ms
            ):
                continue

            realized = dec(item.get("realizedPNL"))
            fee = dec(item.get("fee"))
            funding = dec(item.get("funding"))

            closed.append(
                {
                    "symbol": item.get("symbol") or symbol,
                    "side": item.get("side") or "-",
                    "entry": item.get("entryPrice"),
                    "close": item.get("closePrice"),
                    "realized": realized,
                    "fee": fee,
                    "funding": funding,
                    "net": realized - fee + funding,
                    "mtime": modified,
                }
            )

    open_positions = []

    try:
        for item in get_pending_positions():
            if dec(item.get("qty")) <= 0:
                continue

            open_positions.append(
                {
                    "symbol": item.get("symbol"),
                    "side": item.get("side"),
                    "qty": item.get("qty"),
                    "unrealized": dec(item.get("unrealizedPNL")),
                }
            )
    except Exception:
        pass

    return {
        "closed": closed,
        "open": open_positions,
        "realized": sum(
            (item["realized"] for item in closed),
            Decimal("0"),
        ),
        "fees": sum(
            (item["fee"] for item in closed),
            Decimal("0"),
        ),
        "funding": sum(
            (item["funding"] for item in closed),
            Decimal("0"),
        ),
        "net": sum(
            (item["net"] for item in closed),
            Decimal("0"),
        ),
    }


def v8_summary(start, end, event_file=None, state_file=None):
    event_file = event_file if event_file is not None else V8_EVENT_FILE
    state_file = state_file if state_file is not None else V8_STATE_FILE
    result = {
        "entries": 0,
        "tp1": 0,
        "closed": 0,
        "net": Decimal("0"),
        "wins": 0,
        "losses": 0,
        "position_open": False,
    }

    if event_file.exists():
        start_s = int(start.timestamp())
        end_s = int(end.timestamp())

        with event_file.open(
            "r",
            encoding="utf-8",
        ) as handle:
            for raw_line in handle:
                raw_line = raw_line.strip()

                if not raw_line:
                    continue

                try:
                    row = json.loads(raw_line)
                except Exception:
                    continue

                ts = int(row.get("timestamp") or 0)

                if not (
                    start_s
                    <= ts
                    <= end_s
                ):
                    continue

                event = row.get("event")
                details = row.get("details") or {}

                if event == "PAPER_ENTRY":
                    result["entries"] += 1

                elif event == "PAPER_TP1":
                    result["tp1"] += 1

                elif event == "PAPER_TRADE_CLOSED":
                    pnl = dec(
                        details.get("net_pnl_usdt")
                    )

                    result["closed"] += 1
                    result["net"] += pnl

                    if pnl > 0:
                        result["wins"] += 1
                    else:
                        result["losses"] += 1

    if state_file.exists():
        try:
            state = json.loads(
                state_file.read_text(
                    encoding="utf-8"
                )
            )
            result["position_open"] = bool(
                state.get("position")
            )
        except Exception:
            pass

    return result


def fmt_money(value):
    value = dec(value)
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.4f} USDT"


def build_message(now=None):
    start, end = overnight_window(now)

    live = live_summary(start, end)
    v8 = v8_summary(start, end)
    ada = v8_summary(
        start, end,
        event_file=ADA_EVENT_FILE,
        state_file=ADA_STATE_FILE,
    )
    auto = auto_live_status()

    lines = [
        "LSOB MORGENBERICHT",
        (
            f"{start.strftime('%d.%m. %H:%M')}–"
            f"{end.strftime('%d.%m. %H:%M')} "
            "Europe/Vienna"
        ),
        "",
        "V7 ECHTGELD",
        f"Auto-Live: {'AKTIV' if auto.get('active') else 'AUS'}",
        f"Geschlossene Positionen: {len(live['closed'])}",
        f"Realized PnL vor Gebühren/Funding: {fmt_money(live['realized'])}",
        f"Gebühren: {live['fees']:.4f} USDT",
        f"Funding: {live['funding']:+.4f} USDT",
        f"Netto: {fmt_money(live['net'])}",
    ]

    if live["closed"]:
        lines.append("")

        for item in live["closed"][:8]:
            closed_at = datetime.fromtimestamp(
                item["mtime"] / 1000,
                TZ,
            ).strftime("%H:%M")

            lines.append(
                f"{closed_at} {item['symbol']} {item['side']} "
                f"netto {fmt_money(item['net'])}"
            )

    lines.extend(
        [
            "",
            f"Offene V7-Positionen: {len(live['open'])}",
        ]
    )

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
            f"Entries: {v8['entries']}",
            f"TP1 erreicht: {v8['tp1']}",
            f"Geschlossene Trades: {v8['closed']}",
            f"Gewinner/Verlierer: {v8['wins']}/{v8['losses']}",
            f"Paper-Netto: {fmt_money(v8['net'])}",
            (
                "Paper-Position offen: "
                + ("JA" if v8["position_open"] else "NEIN")
            ),
            "",
            "V8 ADA EXPIRY2 + FVG015 PAPER",
            f"Entries: {ada['entries']}",
            f"TP1 erreicht: {ada['tp1']}",
            f"Geschlossene Trades: {ada['closed']}",
            f"Gewinner/Verlierer: {ada['wins']}/{ada['losses']}",
            (
                "Trefferquote: "
                + (
                    f"{100.0 * ada['wins'] / ada['closed']:.1f}%"
                    if ada["closed"] else "– (noch keine Trades)"
                )
            ),
            f"Paper-Netto: {fmt_money(ada['net'])}",
            (
                "Paper-Position offen: "
                + ("JA" if ada["position_open"] else "NEIN")
            ),
        ]
    )

    if live["closed"] and v8["closed"]:
        lines.extend(
            [
                "",
                "VERGLEICH",
                f"V7 Live netto: {fmt_money(live['net'])}",
                f"V8 Paper netto: {fmt_money(v8['net'])}",
            ]
        )

    lines.extend(
        [
            "",
            "Hinweis: V7-Werte stammen aus der Bitunix-"
            "Positionshistorie. Realized PnL wird separat "
            "von Gebühren und Funding ausgewiesen.",
        ]
    )

    return "\n".join(lines)


def main():
    message = build_message()

    print(message)

    if telegram_configured():
        send_message(message)
        print("")
        print("MORGENBERICHT AN TELEGRAM GESENDET")
    else:
        print("")
        print("Telegram nicht konfiguriert.")


if __name__ == "__main__":
    main()
