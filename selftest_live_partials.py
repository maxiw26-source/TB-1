from unittest.mock import patch

import live_partial_tp as partial
from bitunix_live import LiveExecutionError


def expect_error(fn, text):
    try:
        fn()
    except LiveExecutionError as exc:
        assert text in str(exc)
        return
    raise AssertionError("Fehler wurde erwartet")


def main():
    print("1/5 BTC 70/30 Mengenaufteilung")
    result = partial.calculate_partial_quantities(
        "0.0010",
        70,
        4,
        "0.0001",
    )
    assert result["close_qty"] == "0.0007"
    assert result["remaining_qty"] == "0.0003"

    print("2/5 Zu kleine Restmenge blockieren")
    expect_error(
        lambda: partial.calculate_partial_quantities(
            "0.0001",
            70,
            4,
            "0.0001",
        ),
        "TP1-Teilmenge",
    )

    print("3/5 Reduce-only Payload")
    payload = partial.build_reduce_only_market_payload(
        "BTCUSDT",
        "BUY",
        "0.0007",
        "ONE_WAY",
    )
    assert payload["side"] == "SELL"
    assert payload["reduceOnly"] is True
    assert payload["orderType"] == "MARKET"

    print("4/5 Hedge Close Payload")
    hedge_payload = partial.build_reduce_only_market_payload(
        "BTCUSDT",
        "SHORT",
        "0.001",
        "HEDGE",
    )
    assert hedge_payload["side"] == "BUY"
    assert hedge_payload["tradeSide"] == "CLOSE"

    print("5/5 API Preflight gemockt")
    with patch.object(
        partial,
        "get_trading_pair",
        return_value={
            "basePrecision": 4,
            "minTradeVolume": "0.0001",
        },
    ), patch.object(
        partial,
        "get_pending_positions",
        return_value=[
            {
                "symbol": "BTCUSDT",
                "positionId": "123",
                "qty": "0.0010",
                "side": "BUY",
            }
        ],
    ):
        preflight = partial.partial_preflight(
            "BTCUSDT",
            70,
        )

    assert preflight["quantities"]["close_qty"] == "0.0007"
    assert preflight["quantities"]["remaining_qty"] == "0.0003"

    print("")
    print("LIVE PARTIAL TP SELFTEST ERFOLGREICH")
    print("Keine echte Order wurde gesendet.")


if __name__ == "__main__":
    main()
