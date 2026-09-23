"""Offline V9 causality, stop-first, fee and next-bar-entry checks."""
from copy import deepcopy

from backtest_v9 import (
    START_BALANCE,
    close_position,
    evaluate_exit,
    open_position,
    run_window,
)
from strategy_v9 import PROFILE, indicators, signal


def candle(i, close=100.0, op=None, hi=None, lo=None):
    op = close if op is None else op
    return {
        "timestamp": i * 300_000,
        "open": op,
        "high": close + 0.5 if hi is None else hi,
        "low": close - 0.5 if lo is None else lo,
        "close": close,
        "volume": 10.0,
    }


def main():
    bars = [candle(i, 100 + (i % 9) * 0.15) for i in range(80)]
    past = indicators(bars)
    extended = indicators(bars + [candle(80, 250.0)])
    assert past == extended[:-1], "Future candle changed historical indicators"
    assert past[-1] is not None
    assert past[-1]["vwap"] > 0
    assert 0 <= past[-1]["rsi"] <= 100

    buy = {
        "bb_lower": 100.0, "bb_upper": 108.0,
        "rsi": 20.0, "adx": 10.0,
        "atr": 1.0, "vwap": 103.0,
    }
    assert signal(candle(0, 99), buy) == "LONG"
    assert signal(candle(0, 99), {**buy, "adx": 35}) is None

    sell = {
        "bb_lower": 92.0, "bb_upper": 100.0,
        "rsi": 80.0, "adx": 10.0,
        "atr": 1.0, "vwap": 97.0,
    }
    assert signal(candle(0, 101), sell) == "SHORT"

    p = open_position("LONG", 99.5, 1, 103, START_BALANCE, 300_000)
    assert p is not None
    assert p["entry"] > 99.5, "Entry slippage missing"
    assert p["entry_fee"] > 0, "Entry fee missing"
    both_touch = candle(1, 101, op=99.5, hi=105, lo=96)
    price, reason = evaluate_exit(p, both_touch)
    assert reason == "STOP", "Stop must win when both touched"
    t = close_position(p, price, reason, 300_000)
    assert t["net"] < t["gross"], "Round-trip fees must lower PnL"
    assert t["exit"] < price, "Exit slippage missing"

    features = [buy, None, None]
    two_bars = [
        candle(0, 99.0, op=99, hi=99.8, lo=98.5),
        candle(1, 100, op=99.5, hi=105, lo=96),
        candle(2, 101, op=100.0, hi=101.5, lo=100),
    ]
    r = run_window(two_bars, features, 0, 600_000)
    assert r["count"] == 1
    assert r["trades"][0]["entry_ts"] == 300_000, "Same-bar look-ahead entry"
    assert r["trades"][0]["reason"] == "STOP"
    assert r["fees"] > 0

    print("V9 MEAN-REVERSION SELFTEST ERFOLGREICH")
    print("Past-only indicators, next-bar fill, stop-first, fee+slippage: OK.")
    print("Keine echte Order; keine API-Verbindung.")


if __name__ == "__main__":
    main()
