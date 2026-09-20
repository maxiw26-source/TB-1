import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import bitunix_live as live


def expect_error(fn, contains):
    try:
        fn()
    except live.LiveExecutionError as exc:
        if contains not in str(exc):
            raise AssertionError(
                f"Falscher Fehler: {exc}"
            )
        return

    raise AssertionError(
        "Fehler wurde erwartet"
    )


def approval_now(
    *,
    test=False,
):
    return {
        "status": "APPROVED",
        "created_at": datetime.now(
            timezone.utc
        ).isoformat(),
        "plan": {
            "symbol": "BTCUSDT",
            "side": "BUY",
            "qty": "0.0001",
            "entry": 100.0,
            "stop": 95.0,
            "tp1": 105.0,
            "tp2": 106.0,
            "test": test,
        },
    }


def main():
    original_env = dict(os.environ)
    original_state_file = (
        live.EXECUTION_STATE_FILE
    )

    original_pair = (
        live.get_trading_pair
    )
    original_ticker = live.get_ticker
    original_positions = (
        live.get_pending_positions
    )
    original_orders = (
        live.get_pending_orders
    )

    try:
        with tempfile.TemporaryDirectory() as tmp:
            live.EXECUTION_STATE_FILE = (
                Path(tmp)
                / "execution.json"
            )

            os.environ[
                "LIVE_ALLOWED_SYMBOLS"
            ] = "BTCUSDT,ETHUSDT"
            os.environ[
                "LIVE_MAX_NOTIONAL_USDT"
            ] = "10"
            os.environ[
                "LIVE_MAX_ENTRY_DEVIATION_PERCENT"
            ] = "0.20"
            os.environ[
                "LIVE_MAX_APPROVAL_AGE_SECONDS"
            ] = "120"

            print(
                "1/6 Live-Schalter testen"
            )

            os.environ[
                "ENABLE_LIVE_EXECUTION"
            ] = "false"

            expect_error(
                lambda: (
                    live.preflight_approved_order(
                        approval_now()
                    )
                ),
                "nicht aktiviert",
            )

            print(
                "2/6 Test-Order blockieren"
            )

            os.environ[
                "ENABLE_LIVE_EXECUTION"
            ] = "true"
            os.environ[
                "LIVE_EXECUTION_ACK"
            ] = "I_UNDERSTAND_REAL_ORDERS"

            expect_error(
                lambda: (
                    live.preflight_approved_order(
                        approval_now(
                            test=True
                        )
                    )
                ),
                "Test-Order",
            )

            live.get_trading_pair = (
                lambda symbol: {
                    "symbol": symbol,
                    "minTradeVolume": (
                        "0.0001"
                    ),
                    "maxMarketOrderVolume": (
                        "10"
                    ),
                    "basePrecision": 4,
                    "quotePrecision": 1,
                    "symbolStatus": "OPEN",
                    "isApiSupported": True,
                }
            )

            live.get_ticker = (
                lambda symbol: {
                    "symbol": symbol,
                    "lastPrice": "100.10",
                }
            )

            live.get_pending_positions = (
                lambda symbol=None: []
            )

            live.get_pending_orders = (
                lambda symbol=None: []
            )

            print(
                "3/6 Gültigen Preflight testen"
            )

            approval = approval_now()

            result = (
                live.preflight_approved_order(
                    approval
                )
            )

            if (
                result["symbol"]
                != "BTCUSDT"
            ):
                raise AssertionError(
                    "Symbol falsch"
                )

            if DecimalSafe(
                result[
                    "notional_usdt"
                ]
            ) > DecimalSafe("10"):
                raise AssertionError(
                    "Notional-Limit verletzt"
                )

            print(
                "4/6 Alte Bestätigung blockieren"
            )

            old_approval = approval_now()

            old_approval["created_at"] = (
                datetime.now(
                    timezone.utc
                )
                - timedelta(
                    minutes=10
                )
            ).isoformat()

            expect_error(
                lambda: (
                    live.preflight_approved_order(
                        old_approval
                    )
                ),
                "zu alt",
            )

            print(
                "5/6 Preisabweichung blockieren"
            )

            live.get_ticker = (
                lambda symbol: {
                    "symbol": symbol,
                    "lastPrice": "101.00",
                }
            )

            expect_error(
                lambda: (
                    live.preflight_approved_order(
                        approval_now()
                    )
                ),
                "zu weit",
            )

            print(
                "6/6 Offene Position blockieren"
            )

            live.get_ticker = (
                lambda symbol: {
                    "symbol": symbol,
                    "lastPrice": "100.10",
                }
            )

            live.get_pending_positions = (
                lambda symbol=None: [
                    {
                        "symbol": (
                            "ETHUSDT"
                        ),
                        "qty": "0.003",
                    }
                ]
            )

            expect_error(
                lambda: (
                    live.preflight_approved_order(
                        approval_now()
                    )
                ),
                "offene",
            )

            print("")
            print(
                "LIVE-GUARDRAILS "
                "SELFTEST ERFOLGREICH"
            )

    finally:
        os.environ.clear()
        os.environ.update(
            original_env
        )

        live.EXECUTION_STATE_FILE = (
            original_state_file
        )

        live.get_trading_pair = (
            original_pair
        )
        live.get_ticker = (
            original_ticker
        )
        live.get_pending_positions = (
            original_positions
        )
        live.get_pending_orders = (
            original_orders
        )


def DecimalSafe(value):
    from decimal import Decimal
    return Decimal(str(value))


if __name__ == "__main__":
    main()
