import os
from decimal import Decimal, ROUND_DOWN

from bitunix_live import (
    LiveExecutionError,
    get_pending_positions,
    get_trading_pair,
    private_request,
)


def _fmt_qty(value, precision):
    quantum = Decimal("1").scaleb(-int(precision))
    return format(
        Decimal(str(value)).quantize(
            quantum,
            rounding=ROUND_DOWN,
        ),
        "f",
    )


def calculate_partial_quantities(
    total_qty,
    close_percent,
    base_precision,
    min_trade_volume,
):
    total = Decimal(str(total_qty))
    percent = Decimal(str(close_percent))
    minimum = Decimal(str(min_trade_volume))

    if total <= 0:
        raise LiveExecutionError(
            "Gesamtmenge muss > 0 sein"
        )

    if percent <= 0 or percent >= 100:
        raise LiveExecutionError(
            "Teilverkauf muss zwischen 0 und 100 Prozent liegen"
        )

    close_qty = Decimal(
        _fmt_qty(
            total * percent / Decimal("100"),
            base_precision,
        )
    )

    remaining_qty = total - close_qty

    if close_qty < minimum:
        raise LiveExecutionError(
            "TP1-Teilmenge liegt unter minTradeVolume"
        )

    if remaining_qty < minimum:
        raise LiveExecutionError(
            "Restmenge nach TP1 liegt unter minTradeVolume"
        )

    return {
        "close_qty": _fmt_qty(
            close_qty,
            base_precision,
        ),
        "remaining_qty": _fmt_qty(
            remaining_qty,
            base_precision,
        ),
    }


def choose_live_exit_profile(
    total_qty,
    base_precision,
    min_trade_volume,
    close_percent=70,
    partials_enabled=False,
):
    if not partials_enabled:
        return {
            "profile": "FULL_TP2_PARTIALS_DISABLED",
            "partial_compatible": False,
            "reason": "Live-Teilprofite sind nicht aktiviert",
            "quantities": None,
        }

    try:
        quantities = calculate_partial_quantities(
            total_qty,
            close_percent,
            base_precision,
            min_trade_volume,
        )
    except LiveExecutionError as exc:
        return {
            "profile": "FULL_TP2_MIN_QTY_FALLBACK",
            "partial_compatible": False,
            "reason": str(exc),
            "quantities": None,
        }

    return {
        "profile": "PARTIAL_70_30_READY",
        "partial_compatible": True,
        "reason": None,
        "quantities": quantities,
    }


def build_reduce_only_market_payload(
    symbol,
    position_side,
    qty,
    position_mode="ONE_WAY",
):
    side = str(position_side).upper()

    if side in {"BUY", "LONG"}:
        close_side = "SELL"
    elif side in {"SELL", "SHORT"}:
        close_side = "BUY"
    else:
        raise LiveExecutionError(
            "Unbekannte Positionsseite"
        )

    payload = {
        "symbol": str(symbol).upper(),
        "side": close_side,
        "qty": str(qty),
        "orderType": "MARKET",
        "reduceOnly": True,
    }

    mode = str(position_mode).upper()

    if mode == "HEDGE":
        payload["tradeSide"] = "CLOSE"
    elif mode != "ONE_WAY":
        raise LiveExecutionError(
            "Positionsmodus muss ONE_WAY oder HEDGE sein"
        )

    return payload


def assert_partial_live_enabled():
    if os.getenv(
        "ENABLE_LIVE_PARTIALS",
        "false",
    ).strip().lower() not in {
        "1",
        "true",
        "yes",
        "ja",
        "on",
    }:
        raise LiveExecutionError(
            "ENABLE_LIVE_PARTIALS ist nicht aktiviert"
        )

    if os.getenv(
        "LIVE_PARTIALS_ACK",
        "",
    ).strip() != "I_UNDERSTAND_PARTIAL_EXITS":
        raise LiveExecutionError(
            "LIVE_PARTIALS_ACK fehlt oder ist falsch"
        )


def place_partial_close(
    symbol,
    position_side,
    qty,
    position_mode="ONE_WAY",
):
    assert_partial_live_enabled()

    payload = build_reduce_only_market_payload(
        symbol,
        position_side,
        qty,
        position_mode,
    )

    return private_request(
        "POST",
        "/api/v1/futures/trade/place_order",
        payload=payload,
    )


def place_position_protection(
    symbol,
    position_id,
    stop_price,
    tp2_price,
):
    assert_partial_live_enabled()

    payload = {
        "symbol": str(symbol).upper(),
        "positionId": str(position_id),
        "tpPrice": str(tp2_price),
        "tpStopType": "LAST_PRICE",
        "slPrice": str(stop_price),
        "slStopType": "LAST_PRICE",
    }

    return private_request(
        "POST",
        "/api/v1/futures/tpsl/position/place_order",
        payload=payload,
    )


def move_position_stop_to_break_even(
    symbol,
    position_id,
    break_even_price,
    tp2_price,
):
    assert_partial_live_enabled()

    payload = {
        "symbol": str(symbol).upper(),
        "positionId": str(position_id),
        "tpPrice": str(tp2_price),
        "tpStopType": "LAST_PRICE",
        "slPrice": str(break_even_price),
        "slStopType": "LAST_PRICE",
    }

    return private_request(
        "POST",
        "/api/v1/futures/tpsl/position/modify_order",
        payload=payload,
    )


def get_pending_tpsl_orders(
    symbol=None,
    position_id=None,
):
    params = {
        "limit": 100,
    }

    if symbol:
        params["symbol"] = str(symbol).upper()

    if position_id:
        params["positionId"] = str(position_id)

    result = private_request(
        "GET",
        "/api/v1/futures/tpsl/get_pending_orders",
        params=params,
    )

    data = result.get("data") or []

    if isinstance(data, dict):
        return data.get("orderList", []) or []

    return data


def partial_preflight(
    symbol,
    close_percent,
):
    rules = get_trading_pair(symbol)
    positions = get_pending_positions(symbol)

    live_positions = [
        item
        for item in positions
        if Decimal(str(item.get("qty", "0"))) > 0
    ]

    if len(live_positions) != 1:
        raise LiveExecutionError(
            "Für TP1 muss genau eine offene Position vorhanden sein"
        )

    position = live_positions[0]

    quantities = calculate_partial_quantities(
        position.get("qty"),
        close_percent,
        int(rules.get("basePrecision", 0)),
        rules.get("minTradeVolume", "0"),
    )

    return {
        "position": position,
        "quantities": quantities,
        "rules": {
            "basePrecision": rules.get("basePrecision"),
            "minTradeVolume": rules.get("minTradeVolume"),
        },
    }
