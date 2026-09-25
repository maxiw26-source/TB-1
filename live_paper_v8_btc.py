import json
import os
import sys
import time
from pathlib import Path

import requests

import config
from strategy_v8 import calculate_signal
from telegram_approval import send_message, telegram_configured


BASE_URL = "https://fapi.bitunix.com"
SYMBOL = "BTCUSDT"
POLL_SECONDS = 30

STATE_FILE = Path("v8_paper_state.json")
EVENT_FILE = Path("v8_paper_events.jsonl")

PROFILE = {
    "name": "balanced",
    "swing_lookback": 8,
    "bos_lookback": 8,
    "sweep_to_bos_candles": 10,
    "bos_to_fvg_candles": 5,
    "setup_expiry_candles": 6,
    "min_fvg_atr_ratio": 0.07,
    "sweep_buffer_atr": 0.04,
    "stop_buffer_atr": 0.10,
    "adx_minimum": 18.0,
    "tp1_r": 1.0,
    "tp2_r": 2.0,
}

PAPER_NOTIONAL_USDT = 100.0
EXIT_POLICY = "full_2r_v1"
TP1_CLOSE_PERCENT = 70.0  # Legacy saved positions only.
MAKER_FEE_PERCENT = float(getattr(config, "MAKER_FEE_PERCENT", 0.02))
TAKER_FEE_PERCENT = float(getattr(config, "TAKER_FEE_PERCENT", 0.06))
TAKER_SLIPPAGE_PERCENT = float(getattr(config, "TAKER_SLIPPAGE_PERCENT", 0.02))


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


def default_state():
    return {
        "pending": None,
        "position": None,
        "last_setup_id": None,
        "last_closed_candle": None,
        "stats": {
            "trades": 0,
            "wins": 0,
            "losses": 0,
            "longs": 0,
            "shorts": 0,
            "net_pnl_usdt": 0.0,
        },
    }


def load_state():
    if not STATE_FILE.exists():
        return default_state()

    try:
        payload = json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
    except Exception:
        return default_state()

    state = default_state()
    state.update(payload)

    if "stats" not in state or not isinstance(state["stats"], dict):
        state["stats"] = default_state()["stats"]

    for key, value in default_state()["stats"].items():
        state["stats"].setdefault(key, value)

    return state


def save_state(state):
    temp = STATE_FILE.with_suffix(".tmp")
    temp.write_text(
        json.dumps(
            state,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    temp.replace(STATE_FILE)


def log_event(event, details=None):
    row = {
        "timestamp": int(time.time()),
        "event": event,
        "details": details or {},
    }

    with EVENT_FILE.open(
        "a",
        encoding="utf-8",
    ) as handle:
        handle.write(
            json.dumps(
                row,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            + "\n"
        )


def notify(text):
    if not telegram_configured():
        return

    try:
        send_message(text)
    except Exception as exc:
        log_event(
            "TELEGRAM_ERROR",
            {"error": str(exc)},
        )


def get_klines(interval, limit=300):
    response = requests.get(
        BASE_URL + "/api/v1/futures/market/kline",
        params={
            "symbol": SYMBOL,
            "interval": interval,
            "limit": limit,
        },
        timeout=10,
    )
    response.raise_for_status()

    result = response.json()

    if result.get("code") != 0:
        raise RuntimeError(result)

    candles = []

    for item in result.get("data", []):
        candles.append(
            {
                "timestamp": int(item["time"]),
                "open": float(item["open"]),
                "high": float(item["high"]),
                "low": float(item["low"]),
                "close": float(item["close"]),
                "volume": float(item.get("baseVol", 0) or 0),
            }
        )

    candles.sort(key=lambda candle: candle["timestamp"])
    return candles


def closed_candles(interval, limit=300):
    candles = get_klines(interval, limit=limit)

    interval_ms = {
        "1m": 60_000,
        "5m": 300_000,
        "15m": 900_000,
    }[interval]

    now_ms = int(time.time() * 1000)

    return [
        candle
        for candle in candles
        if candle["timestamp"] + interval_ms <= now_ms
    ]


def maker_fee(price, qty):
    return price * qty * MAKER_FEE_PERCENT / 100.0


def taker_fee(price, qty):
    return price * qty * TAKER_FEE_PERCENT / 100.0


def slippage(price, qty):
    return price * qty * TAKER_SLIPPAGE_PERCENT / 100.0


def build_position(setup):
    side = setup["side"]
    entry = float(setup["entry"])
    stop = float(setup["stop"])

    risk = (
        entry - stop
        if side == "LONG"
        else stop - entry
    )

    if risk <= 0:
        raise ValueError("Ungültige Risk-Distanz")

    tp1 = (
        entry + risk * PROFILE["tp1_r"]
        if side == "LONG"
        else entry - risk * PROFILE["tp1_r"]
    )

    tp2 = (
        entry + risk * PROFILE["tp2_r"]
        if side == "LONG"
        else entry - risk * PROFILE["tp2_r"]
    )

    qty = PAPER_NOTIONAL_USDT / entry

    return {
        "side": side,
        "exit_policy": EXIT_POLICY,
        "initial_stop": stop,
        "entry": entry,
        "paper_notional_usdt": PAPER_NOTIONAL_USDT,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "qty": qty,
        "remaining": qty,
        "tp1_hit": False,
        "gross": 0.0,
        "fees": maker_fee(entry, qty),
        "slippage": 0.0,
        "opened_at": int(time.time()),
    }


def close_part(position, price, qty, reason):
    qty = min(float(qty), float(position["remaining"]))

    if qty <= 0:
        return

    if position["side"] == "LONG":
        position["gross"] += (
            float(price)
            - float(position["entry"])
        ) * qty
    else:
        position["gross"] += (
            float(position["entry"])
            - float(price)
        ) * qty

    if reason == "STOP":
        position["fees"] += taker_fee(price, qty)
        position["slippage"] += slippage(price, qty)
    else:
        position["fees"] += maker_fee(price, qty)

    position["remaining"] -= qty
    if position["remaining"] < 1e-12:
        position["remaining"] = 0.0


def check_position(position, candle):
    high = float(candle["high"])
    low = float(candle["low"])

    if position.get("exit_policy") == EXIT_POLICY:
        is_long = position["side"] == "LONG"
        stop_hit = low <= position["stop"] if is_long else high >= position["stop"]
        target_hit = high >= position["tp2"] if is_long else low <= position["tp2"]
        # Conservative ordering when both barriers occur in one candle.
        if stop_hit or target_hit:
            reason = "STOP" if stop_hit else "TP2"
            price = position["stop"] if stop_hit else position["tp2"]
            close_part(position, price, position["remaining"], reason)
            return reason
        return None

    if position["side"] == "LONG":
        if low <= float(position["stop"]):
            close_part(
                position,
                float(position["stop"]),
                position["remaining"],
                "STOP",
            )
            return "STOP"

        if (
            not position["tp1_hit"]
            and high >= float(position["tp1"])
        ):
            close_part(
                position,
                float(position["tp1"]),
                float(position["qty"])
                * TP1_CLOSE_PERCENT
                / 100.0,
                "TP1",
            )
            position["tp1_hit"] = True
            position["stop"] = float(position["entry"])
            return "TP1"

        if high >= float(position["tp2"]):
            close_part(
                position,
                float(position["tp2"]),
                position["remaining"],
                "TP2",
            )
            return "TP2"

    else:
        if high >= float(position["stop"]):
            close_part(
                position,
                float(position["stop"]),
                position["remaining"],
                "STOP",
            )
            return "STOP"

        if (
            not position["tp1_hit"]
            and low <= float(position["tp1"])
        ):
            close_part(
                position,
                float(position["tp1"]),
                float(position["qty"])
                * TP1_CLOSE_PERCENT
                / 100.0,
                "TP1",
            )
            position["tp1_hit"] = True
            position["stop"] = float(position["entry"])
            return "TP1"

        if low <= float(position["tp2"]):
            close_part(
                position,
                float(position["tp2"]),
                position["remaining"],
                "TP2",
            )
            return "TP2"

    return None


def close_trade(state, reason):
    position = state["position"]

    net = (
        float(position["gross"])
        - float(position["fees"])
        - float(position["slippage"])
    )

    stats = state["stats"]
    stats["trades"] += 1
    stats["net_pnl_usdt"] += net

    # Preserve the running totals, but separately track trades opened with
    # this size. Positions saved before the size change still close at 10 USDT.
    notional = float(position.get("paper_notional_usdt") or
                     float(position["entry"]) * float(position["qty"]))
    size_key = f"{notional:.2f}"
    size_stats = state.setdefault("stats_by_notional", {}).setdefault(
        size_key, {"trades": 0, "net_pnl_usdt": 0.0}
    )
    size_stats["trades"] += 1
    size_stats["net_pnl_usdt"] += net

    policy = position.get("exit_policy", "legacy_partial")
    policy_stats = state.setdefault("stats_by_exit_policy", {}).setdefault(
        policy, {"trades": 0, "wins": 0, "losses": 0, "net_pnl_usdt": 0.0}
    )
    policy_stats["trades"] += 1
    policy_stats["wins" if net > 0 else "losses"] += 1
    policy_stats["net_pnl_usdt"] += net

    if position["side"] == "LONG":
        stats["longs"] += 1
    else:
        stats["shorts"] += 1

    if net > 0:
        stats["wins"] += 1
    else:
        stats["losses"] += 1

    details = {
        "side": position["side"],
        "reason": reason,
        "exit_policy": policy,
        "paper_notional_usdt": notional,
        "net_pnl_usdt": net,
        "entry": position["entry"],
        "tp1": position["tp1"],
        "tp2": position["tp2"],
        "stop": position["stop"],
    }

    log_event("PAPER_TRADE_CLOSED", details)

    notify(
        "LSOB V8 BTC PAPER – Trade beendet\n"
        f"Seite: {position['side']}\n"
        f"Grund: {reason}\n"
        f"Netto: {net:.4f} USDT\n"
        f"Trades gesamt: {stats['trades']}\n"
        f"Paper-PnL gesamt: {stats['net_pnl_usdt']:.4f} USDT"
    )

    state["position"] = None


def process_candle(state, candle):
    if state["position"] is not None:
        event = check_position(
            state["position"],
            candle,
        )

        if event == "TP1":
            log_event(
                "PAPER_TP1",
                state["position"],
            )
            notify(
                "LSOB V8 BTC PAPER – TP1 erreicht\n"
                f"Seite: {state['position']['side']}\n"
                "70 % Paper-Teilprofit, Stop jetzt Break-even."
            )
            return

        if event in {"TP2", "STOP"}:
            close_trade(state, event)
            return

    pending = state["pending"]

    if pending is not None:
        setup = pending["setup"]
        age = (
            int(candle["timestamp"])
            - int(pending["created_candle_ts"])
        ) // 60_000

        invalid = (
            float(candle["low"]) <= float(setup["stop"])
            if setup["side"] == "LONG"
            else float(candle["high"]) >= float(setup["stop"])
        )

        if invalid:
            log_event("PENDING_INVALIDATED", pending)
            state["pending"] = None

        elif age > int(PROFILE["setup_expiry_candles"]):
            log_event("PENDING_EXPIRED", pending)
            state["pending"] = None

        else:
            entry = float(setup["entry"])

            if (
                float(candle["low"])
                <= entry
                <= float(candle["high"])
            ):
                position = build_position(setup)
                state["position"] = position
                state["pending"] = None

                log_event(
                    "PAPER_ENTRY",
                    position,
                )

                notify(
                    "LSOB V8 BTC PAPER – Entry\n"
                    f"Seite: {position['side']}\n"
                    f"Entry: {position['entry']:.2f}\n"
                    f"Stop: {position['stop']:.2f}\n"
                    "Ausstieg: komplett bei 2R, kein Teilverkauf.\n"
                    f"TP2: {position['tp2']:.2f}\n"
                    f"Paper-Notional: {PAPER_NOTIONAL_USDT:.2f} USDT\n"
                    "KEINE echte Order."
                )


def analyze_for_setup(state):
    if (
        state["position"] is not None
        or state["pending"] is not None
    ):
        return

    entry = closed_candles("1m", limit=300)
    confirmation = closed_candles("5m", limit=300)
    trend = closed_candles("15m", limit=300)

    result = calculate_signal(
        entry,
        confirmation,
        trend,
        PROFILE,
    )

    signal = result.get("signal")

    if signal not in {
        "PENDING_LONG",
        "PENDING_SHORT",
    }:
        return

    indicators = result.get("indicators") or {}
    setup_id = indicators.get("setup_id")
    setup = indicators.get("setup")

    if (
        not setup_id
        or not setup
        or setup_id == state.get("last_setup_id")
    ):
        return

    state["last_setup_id"] = setup_id

    latest_closed = entry[-1]

    state["pending"] = {
        "setup_id": setup_id,
        "setup": setup,
        "created_candle_ts": int(
            latest_closed["timestamp"]
        ),
    }

    log_event(
        "PAPER_PENDING_CREATED",
        state["pending"],
    )


def status_text(state):
    stats = state["stats"]
    size_stats = state.get("stats_by_notional", {}).get(
        f"{PAPER_NOTIONAL_USDT:.2f}", {}
    )

    policy_stats = state.get("stats_by_exit_policy", {}).get(EXIT_POLICY, {})

    return (
        "LSOB V8 BTC PAPER Status\n"
        f"Profil: {PROFILE['name']}\n"
        "Neue Trades: Risiko:Ertrag 1:2 vor Kosten; voller TP, kein Break-even.\n"
        f"2R-Phase: {policy_stats.get('trades', 0)} Trades, "
        f"{policy_stats.get('net_pnl_usdt', 0.0):+.4f} USDT\n"
        f"Neue Positionen: {PAPER_NOTIONAL_USDT:.2f} USDT\n"
        f"Pending: {'JA' if state['pending'] else 'NEIN'}\n"
        f"Position: {'JA' if state['position'] else 'NEIN'}\n"
        f"Trades: {stats['trades']}\n"
        f"Gewinner/Verlierer: {stats['wins']}/{stats['losses']}\n"
        f"LONG/SHORT: {stats['longs']}/{stats['shorts']}\n"
        f"Paper-PnL: {stats['net_pnl_usdt']:.4f} USDT\n"
        f"Davon {PAPER_NOTIONAL_USDT:.0f}-USDT-Phase: "
        f"{size_stats.get('trades', 0)} Trades, "
        f"{size_stats.get('net_pnl_usdt', 0.0):+.4f} USDT\n"
        "Gesamtwerte enthalten fruehere Positionsgroessen.\n"
        "ECHTGELD: AUS"
    )


def run():
    state = load_state()

    print("LSOB V8 BTC balanced PAPER-Monitor läuft.")
    print("Keine echte Order-Funktion vorhanden.")

    while True:
        try:
            one_minute = closed_candles("1m", limit=5)

            if not one_minute:
                time.sleep(POLL_SECONDS)
                continue

            candle = one_minute[-1]
            candle_ts = int(candle["timestamp"])

            if state.get("last_closed_candle") != candle_ts:
                state["last_closed_candle"] = candle_ts
                process_candle(state, candle)
                analyze_for_setup(state)
                save_state(state)

            time.sleep(POLL_SECONDS)

        except KeyboardInterrupt:
            save_state(state)
            return

        except Exception as exc:
            log_event(
                "LOOP_ERROR",
                {"error": str(exc)},
            )
            print("V8 PAPER Fehler:", exc)
            time.sleep(POLL_SECONDS)


def main():
    command = (
        sys.argv[1].lower()
        if len(sys.argv) > 1
        else "run"
    )

    state = load_state()

    if command == "run":
        run()
        return

    if command == "status":
        print(
            json.dumps(
                state,
                indent=2,
                ensure_ascii=False,
            )
        )
        print("")
        print(status_text(state))
        return

    print("Verfügbar: run, status")


if __name__ == "__main__":
    main()
