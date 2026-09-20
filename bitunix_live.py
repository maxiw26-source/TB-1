import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import requests


BASE_URL = "https://fapi.bitunix.com"
EXECUTION_STATE_FILE = Path(
    "live_execution_state.json"
)


class LiveExecutionError(RuntimeError):
    pass


def env_bool(name, default=False):
    raw = os.getenv(name)

    if raw is None:
        return bool(default)

    return raw.strip().lower() in {
        "1",
        "true",
        "yes",
        "ja",
        "on",
    }


def env_decimal(name, default):
    raw = os.getenv(name)

    if raw is None:
        return Decimal(str(default))

    try:
        return Decimal(raw.strip())
    except InvalidOperation as exc:
        raise LiveExecutionError(
            f"Ungültiger Wert für {name}"
        ) from exc


def allowed_symbols():
    raw = os.getenv(
        "LIVE_ALLOWED_SYMBOLS",
        "BTCUSDT,ETHUSDT",
    )

    return {
        item.strip().upper()
        for item in raw.split(",")
        if item.strip()
    }


def _credentials():
    api_key = os.getenv("BITUNIX_API_KEY")
    secret_key = os.getenv(
        "BITUNIX_SECRET_KEY"
    )

    if not api_key or not secret_key:
        raise LiveExecutionError(
            "BITUNIX_API_KEY oder "
            "BITUNIX_SECRET_KEY fehlt"
        )

    return api_key, secret_key


def _sha256_hex(value):
    return hashlib.sha256(
        value.encode("utf-8")
    ).hexdigest()


def _query_string(params):
    if not params:
        return ""

    return "".join(
        f"{key}{params[key]}"
        for key in sorted(params)
        if params[key] is not None
    )


def _body_string(payload):
    if payload is None:
        return ""

    return json.dumps(
        payload,
        separators=(",", ":"),
        ensure_ascii=False,
    )


def _signed_headers(
    params=None,
    body="",
):
    api_key, secret_key = (
        _credentials()
    )

    nonce = uuid.uuid4().hex
    timestamp = str(
        int(time.time() * 1000)
    )

    digest = _sha256_hex(
        nonce
        + timestamp
        + api_key
        + _query_string(params)
        + body
    )

    signature = _sha256_hex(
        digest + secret_key
    )

    return {
        "api-key": api_key,
        "nonce": nonce,
        "timestamp": timestamp,
        "sign": signature,
        "language": "en-US",
        "Content-Type": "application/json",
    }


def private_request(
    method,
    path,
    params=None,
    payload=None,
):
    body = _body_string(payload)

    response = requests.request(
        method,
        BASE_URL + path,
        params=params,
        data=body if body else None,
        headers=_signed_headers(
            params=params,
            body=body,
        ),
        timeout=10,
    )

    response.raise_for_status()

    result = response.json()

    if result.get("code") != 0:
        raise LiveExecutionError(
            f"Bitunix API Fehler: {result}"
        )

    return result


def public_get(
    path,
    params=None,
):
    response = requests.get(
        BASE_URL + path,
        params=params,
        timeout=10,
    )

    response.raise_for_status()

    result = response.json()

    if result.get("code") != 0:
        raise LiveExecutionError(
            f"Bitunix API Fehler: {result}"
        )

    return result


def get_trading_pair(symbol):
    result = public_get(
        (
            "/api/v1/futures/market/"
            "trading_pairs"
        ),
        params={
            "symbols": symbol,
        },
    )

    data = result.get("data", [])

    for item in data:
        if item.get("symbol") == symbol:
            return item

    raise LiveExecutionError(
        f"Trading Pair nicht gefunden: "
        f"{symbol}"
    )


def get_ticker(symbol):
    result = public_get(
        "/api/v1/futures/market/tickers",
        params={
            "symbols": symbol,
        },
    )

    data = result.get("data", [])

    for item in data:
        if item.get("symbol") == symbol:
            return item

    raise LiveExecutionError(
        f"Ticker nicht gefunden: {symbol}"
    )


def get_pending_positions(
    symbol=None,
):
    params = None

    if symbol:
        params = {
            "symbol": symbol,
        }

    result = private_request(
        "GET",
        (
            "/api/v1/futures/position/"
            "get_pending_positions"
        ),
        params=params,
    )

    return result.get("data", []) or []


def get_pending_orders(
    symbol=None,
):
    params = {
        "limit": 100,
    }

    if symbol:
        params["symbol"] = symbol

    result = private_request(
        "GET",
        (
            "/api/v1/futures/trade/"
            "get_pending_orders"
        ),
        params=params,
    )

    data = result.get("data") or {}

    return data.get(
        "orderList",
        [],
    ) or []


def get_account_balance():
    return private_request(
        "GET",
        "/api/v1/cp/asset/query",
    ).get("data") or {}


def _decimal_places(value):
    decimal_value = Decimal(
        str(value)
    )

    exponent = decimal_value.as_tuple(
    ).exponent

    return max(
        0,
        -exponent,
    )


def _validate_quantity(
    qty,
    rules,
):
    try:
        qty_decimal = Decimal(
            str(qty)
        )
    except InvalidOperation as exc:
        raise LiveExecutionError(
            "Ungültige Ordermenge"
        ) from exc

    if qty_decimal <= 0:
        raise LiveExecutionError(
            "Ordermenge muss > 0 sein"
        )

    minimum = Decimal(
        str(
            rules.get(
                "minTradeVolume",
                "0",
            )
        )
    )

    maximum = Decimal(
        str(
            rules.get(
                "maxMarketOrderVolume",
                "0",
            )
        )
    )

    precision = int(
        rules.get(
            "basePrecision",
            0,
        )
    )

    if qty_decimal < minimum:
        raise LiveExecutionError(
            "Ordermenge liegt unter "
            f"minTradeVolume ({minimum})"
        )

    if (
        maximum > 0
        and qty_decimal > maximum
    ):
        raise LiveExecutionError(
            "Ordermenge liegt über "
            "maxMarketOrderVolume"
        )

    if _decimal_places(qty) > precision:
        raise LiveExecutionError(
            "Ordermenge hat zu viele "
            "Dezimalstellen"
        )

    return qty_decimal


def _parse_iso(value):
    if not value:
        return None

    try:
        return datetime.fromisoformat(
            value.replace(
                "Z",
                "+00:00",
            )
        )
    except ValueError:
        return None


def build_client_id(
    approval,
):
    plan = approval["plan"]

    raw = "|".join(
        [
            str(
                approval.get(
                    "created_at",
                    "",
                )
            ),
            str(plan.get("symbol", "")),
            str(plan.get("side", "")),
            str(plan.get("qty", "")),
            str(plan.get("entry", "")),
        ]
    )

    digest = hashlib.sha256(
        raw.encode("utf-8")
    ).hexdigest()[:18]

    return "lsob7" + digest


def _load_execution_state():
    if not EXECUTION_STATE_FILE.exists():
        return {
            "attempts": {},
        }

    try:
        return json.loads(
            EXECUTION_STATE_FILE.read_text(
                encoding="utf-8"
            )
        )
    except (
        OSError,
        json.JSONDecodeError,
    ):
        return {
            "attempts": {},
        }


def _save_execution_state(state):
    temp_file = (
        EXECUTION_STATE_FILE.with_suffix(
            ".tmp"
        )
    )

    temp_file.write_text(
        json.dumps(
            state,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    temp_file.replace(
        EXECUTION_STATE_FILE
    )


def _register_attempt(
    client_id,
    status,
    payload=None,
    response=None,
    error=None,
):
    state = _load_execution_state()

    state.setdefault(
        "attempts",
        {},
    )

    state["attempts"][client_id] = {
        "status": status,
        "updated_at": (
            datetime.now(
                timezone.utc
            ).isoformat()
        ),
        "payload": payload,
        "response": response,
        "error": error,
    }

    _save_execution_state(
        state
    )


def _assert_not_attempted(
    client_id,
):
    state = _load_execution_state()

    existing = (
        state.get(
            "attempts",
            {},
        ).get(client_id)
    )

    if not existing:
        return

    raise LiveExecutionError(
        "Dieser bestätigte Plan wurde "
        "bereits für eine Live-Ausführung "
        "registriert. Automatische "
        "Wiederholung wird blockiert."
    )


def preflight_approved_order(
    approval,
):
    if not env_bool(
        "ENABLE_LIVE_EXECUTION",
        False,
    ):
        raise LiveExecutionError(
            "ENABLE_LIVE_EXECUTION ist "
            "nicht aktiviert"
        )

    if (
        not approval
        or approval.get("status")
        != "APPROVED"
    ):
        raise LiveExecutionError(
            "Order ist nicht bestätigt"
        )

    plan = approval.get("plan") or {}

    if plan.get("test"):
        raise LiveExecutionError(
            "Test-Order darf niemals live "
            "ausgeführt werden"
        )

    symbol = str(
        plan.get("symbol", "")
    ).upper()

    if symbol not in allowed_symbols():
        raise LiveExecutionError(
            f"Symbol nicht freigegeben: "
            f"{symbol}"
        )

    side = str(
        plan.get("side", "")
    ).upper()

    if side not in {
        "BUY",
        "SELL",
    }:
        raise LiveExecutionError(
            "Ungültige Orderseite"
        )

    created_at = _parse_iso(
        approval.get("created_at")
    )

    if created_at is None:
        raise LiveExecutionError(
            "Approval-Zeit fehlt"
        )

    now = datetime.now(
        timezone.utc
    )

    age_seconds = (
        now - created_at
    ).total_seconds()

    max_age = float(
        env_decimal(
            "LIVE_MAX_APPROVAL_AGE_SECONDS",
            "120",
        )
    )

    if age_seconds < 0:
        raise LiveExecutionError(
            "Approval-Zeit liegt in "
            "der Zukunft"
        )

    if age_seconds > max_age:
        raise LiveExecutionError(
            "Bestätigung ist zu alt "
            f"({age_seconds:.1f}s)"
        )

    rules = get_trading_pair(
        symbol
    )

    if str(
        rules.get(
            "symbolStatus",
            "",
        )
    ).upper() != "OPEN":
        raise LiveExecutionError(
            "Trading Pair ist nicht OPEN"
        )

    if not bool(
        rules.get(
            "isApiSupported",
            False,
        )
    ):
        raise LiveExecutionError(
            "API-Trading ist für das "
            "Symbol deaktiviert"
        )

    qty = _validate_quantity(
        plan.get("qty"),
        rules,
    )

    ticker = get_ticker(
        symbol
    )

    last_price = Decimal(
        str(
            ticker.get(
                "lastPrice"
            )
            or ticker.get(
                "last"
            )
        )
    )

    if last_price <= 0:
        raise LiveExecutionError(
            "Ungültiger Marktpreis"
        )

    planned_entry = Decimal(
        str(plan.get("entry"))
    )

    stop = Decimal(
        str(plan.get("stop"))
    )

    tp1 = Decimal(
        str(plan.get("tp1"))
    )

    tp2 = Decimal(
        str(plan.get("tp2"))
    )

    if planned_entry <= 0:
        raise LiveExecutionError(
            "Ungültiger geplanter Entry"
        )

    deviation_pct = (
        abs(
            last_price
            - planned_entry
        )
        / planned_entry
        * Decimal("100")
    )

    max_deviation = env_decimal(
        "LIVE_MAX_ENTRY_DEVIATION_PERCENT",
        "0.20",
    )

    if deviation_pct > max_deviation:
        raise LiveExecutionError(
            "Marktpreis ist zu weit vom "
            "geplanten Entry entfernt "
            f"({deviation_pct:.4f}%)"
        )

    if side == "BUY":
        if not (
            stop
            < last_price
            < tp1
            < tp2
        ):
            raise LiveExecutionError(
                "LONG-Preisstruktur ist "
                "nicht mehr gültig"
            )
    else:
        if not (
            stop
            > last_price
            > tp1
            > tp2
        ):
            raise LiveExecutionError(
                "SHORT-Preisstruktur ist "
                "nicht mehr gültig"
            )

    notional = (
        qty * last_price
    )

    max_notional = env_decimal(
        "LIVE_MAX_NOTIONAL_USDT",
        "10.00",
    )

    if notional > max_notional:
        raise LiveExecutionError(
            "Maximales Live-Notional "
            "überschritten "
            f"({notional} > "
            f"{max_notional} USDT)"
        )

    positions = (
        get_pending_positions()
    )

    if positions:
        raise LiveExecutionError(
            "Es existiert bereits "
            "mindestens eine offene "
            "Futures-Position"
        )

    pending_orders = (
        get_pending_orders()
    )

    if pending_orders:
        raise LiveExecutionError(
            "Es existiert bereits "
            "mindestens eine offene "
            "Futures-Order"
        )

    client_id = build_client_id(
        approval
    )

    _assert_not_attempted(
        client_id
    )

    return {
        "symbol": symbol,
        "side": side,
        "qty": str(plan["qty"]),
        "entry": str(
            planned_entry
        ),
        "last_price": str(
            last_price
        ),
        "stop": str(stop),
        "tp1": str(tp1),
        "tp2": str(tp2),
        "notional_usdt": str(
            notional
        ),
        "entry_deviation_percent": str(
            deviation_pct
        ),
        "client_id": client_id,
        "rules": {
            "minTradeVolume": (
                rules.get(
                    "minTradeVolume"
                )
            ),
            "basePrecision": (
                rules.get(
                    "basePrecision"
                )
            ),
            "quotePrecision": (
                rules.get(
                    "quotePrecision"
                )
            ),
        },
    }


def place_approved_entry(
    approval,
):
    preflight = (
        preflight_approved_order(
            approval
        )
    )

    plan = approval["plan"]

    client_id = preflight[
        "client_id"
    ]

    payload = {
        "symbol": preflight["symbol"],
        "side": preflight["side"],
        "qty": preflight["qty"],
        "orderType": "MARKET",
        "reduceOnly": False,
        "clientId": client_id,
        "tpPrice": str(plan["tp2"]),
        "tpStopType": "LAST_PRICE",
        "tpOrderType": "MARKET",
        "slPrice": str(plan["stop"]),
        "slStopType": "LAST_PRICE",
        "slOrderType": "MARKET",
    }

    position_mode = os.getenv(
        "BITUNIX_POSITION_MODE",
        "ONE_WAY",
    ).strip().upper()

    if position_mode == "HEDGE":
        payload["tradeSide"] = "OPEN"
    elif position_mode != "ONE_WAY":
        raise LiveExecutionError(
            "BITUNIX_POSITION_MODE muss "
            "ONE_WAY oder HEDGE sein"
        )

    _register_attempt(
        client_id,
        "PREPARED",
        payload=payload,
    )

    try:
        response = private_request(
            "POST",
            (
                "/api/v1/futures/trade/"
                "place_order"
            ),
            payload=payload,
        )
    except Exception as exc:
        _register_attempt(
            client_id,
            "UNCERTAIN",
            payload=payload,
            error=str(exc),
        )
        raise

    _register_attempt(
        client_id,
        "SENT",
        payload=payload,
        response=response,
    )

    return {
        "preflight": preflight,
        "payload": payload,
        "response": response,
    }


def live_execution_status():
    return {
        "enabled": env_bool(
            "ENABLE_LIVE_EXECUTION",
            False,
        ),
        "allowed_symbols": sorted(
            allowed_symbols()
        ),
        "max_notional_usdt": str(
            env_decimal(
                "LIVE_MAX_NOTIONAL_USDT",
                "10.00",
            )
        ),
        "max_entry_deviation_percent": str(
            env_decimal(
                "LIVE_MAX_ENTRY_DEVIATION_PERCENT",
                "0.20",
            )
        ),
        "max_approval_age_seconds": str(
            env_decimal(
                "LIVE_MAX_APPROVAL_AGE_SECONDS",
                "120",
            )
        ),
        "position_mode": os.getenv(
            "BITUNIX_POSITION_MODE",
            "ONE_WAY",
        ).strip().upper(),
        "api_key_loaded": bool(
            os.getenv("BITUNIX_API_KEY")
        ),
        "secret_loaded": bool(
            os.getenv(
                "BITUNIX_SECRET_KEY"
            )
        ),
        "execution_state_exists": (
            EXECUTION_STATE_FILE.exists()
        ),
    }
