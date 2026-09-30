import time
from decimal import Decimal, ROUND_DOWN

import live_paper_v8_ada as ada
from bitunix_live import (
    LiveExecutionError,
    get_trading_pair,
    place_approved_entry,
)
from telegram_approval import send_message, telegram_configured
from v8_ada_approval_runtime import (
    clear_v8_ada_approval,
    create_v8_ada_approval,
    read_v8_ada_approval,
)

paper = ada.paper
SYMBOL = "ADAUSDT"
MAX_NOTIONAL_USDT = Decimal("10")


def notify(text):
    if telegram_configured():
        try:
            send_message(text)
        except Exception as exc:
            paper.log_event("V8_ADA_TELEGRAM_ERROR", {"error": str(exc)})


def floor_qty_for_10_usdt(entry):
    rules = get_trading_pair(SYMBOL)
    precision = int(rules.get("basePrecision", 0))
    quantum = Decimal("1").scaleb(-precision)
    qty = (MAX_NOTIONAL_USDT / Decimal(str(entry))).quantize(
        quantum, rounding=ROUND_DOWN
    )
    minimum = Decimal(str(rules.get("minTradeVolume", "0")))
    if qty <= 0 or qty < minimum:
        raise LiveExecutionError(
            f"10-USDT-Menge unter minTradeVolume ({minimum})"
        )
    return format(qty, "f")


def build_live_plan(position):
    side = "BUY" if position["side"] == "LONG" else "SELL"
    return {
        "symbol": SYMBOL,
        "side": side,
        "qty": floor_qty_for_10_usdt(position["entry"]),
        "entry": float(position["entry"]),
        "stop": float(position["initial_stop"]),
        "tp1": float(position["tp1"]),
        "tp2": float(position["tp2"]),
    }


def create_live_approval(position):
    existing = read_v8_ada_approval()
    if existing and existing.get("status") in {"WAITING", "APPROVED"}:
        return

    plan = build_live_plan(position)
    create_v8_ada_approval(plan)
    paper.log_event("V8_ADA_LIVE_APPROVAL_CREATED", plan)
    notify(
        "V8 ADA – Echtgeld-Bestätigung erforderlich\n"
        "Maximal: 10 USDT · 1x\n"
        f"Seite: {plan['side']}\n"
        f"Menge: {plan['qty']} ADA\n"
        f"Entry: {plan['entry']}\n"
        f"Stop: {plan['stop']}\n"
        f"TP2: {plan['tp2']}\n\n"
        "Freigeben: /approve_v8ada\n"
        "Ablehnen: /reject_v8ada\n"
        "Status: /status_v8ada"
    )


def consume_approval(state):
    approval = read_v8_ada_approval()
    if not approval:
        return

    status = approval.get("status")

    if status == "REJECTED":
        paper.log_event("V8_ADA_LIVE_REJECTED", approval.get("plan") or {})
        clear_v8_ada_approval()
        return

    if status != "APPROVED":
        return

    if state.get("position") is None:
        paper.log_event(
            "V8_ADA_LIVE_BLOCKED",
            {"reason": "Paper-Position nicht mehr offen"},
        )
        notify(
            "V8 ADA – Live-Order blockiert: "
            "Paper-Position ist nicht mehr offen."
        )
        clear_v8_ada_approval()
        return

    try:
        result = place_approved_entry(approval)
    except Exception as exc:
        paper.log_event("V8_ADA_LIVE_BLOCKED", {"error": str(exc)})
        notify(f"V8 ADA – Live-Order blockiert: {exc}")
        clear_v8_ada_approval()
        return

    preflight = result.get("preflight") or {}
    paper.log_event(
        "V8_ADA_LIVE_ENTRY_CONFIRMED",
        {
            "symbol": SYMBOL,
            "notional_usdt": preflight.get("notional_usdt"),
            "client_id": preflight.get("client_id"),
        },
    )
    notify(
        "V8 ADA – Echtgeld-Order ausgeführt und bestätigt.\n"
        f"Notional: {preflight.get('notional_usdt')} USDT\n"
        "Exchange-SL und TP2 wurden mitgesendet."
    )
    clear_v8_ada_approval()


def run():
    state = paper.load_state()
    print("V8 ADA approval-gated live runner läuft.", flush=True)
    print("Max 10 USDT, echte Order nur nach /approve_v8ada.", flush=True)

    while True:
        try:
            one_minute = paper.closed_candles("1m", limit=5)
            if one_minute:
                candle = one_minute[-1]
                candle_ts = int(candle["timestamp"])

                if state.get("last_closed_candle") != candle_ts:
                    had_position = state.get("position") is not None
                    state["last_closed_candle"] = candle_ts
                    paper.process_candle(state, candle)
                    paper.analyze_for_setup(state)
                    paper.save_state(state)

                    if (
                        not had_position
                        and state.get("position") is not None
                    ):
                        create_live_approval(state["position"])

            consume_approval(state)
            time.sleep(paper.POLL_SECONDS)

        except KeyboardInterrupt:
            paper.save_state(state)
            return
        except Exception as exc:
            paper.log_event("V8_ADA_LIVE_LOOP_ERROR", {"error": str(exc)})
            print("V8 ADA LIVE Fehler:", exc, flush=True)
            time.sleep(paper.POLL_SECONDS)


if __name__ == "__main__":
    run()
