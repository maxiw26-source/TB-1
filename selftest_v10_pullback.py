"""V10 offline timing, filtering and execution checks."""
from backtest_v10_pullback import (
    CANDLE_MS, FIFTEEN_MS, aggregate_15m, ema_series, exit_hit, open_trade, run_window,
    setup_side, trend_at_5m,
)


def candle(ts, price, op=None, high=None, low=None):
    return {"timestamp": ts, "open": price if op is None else op,
            "high": price + 0.5 if high is None else high,
            "low": price - 0.5 if low is None else low,
            "close": price, "volume": 10.0}


def main():
    small = [candle(i * CANDLE_MS, 100 + i) for i in range(9)]
    aggregated = aggregate_15m(small)
    assert [c["timestamp"] for c in aggregated] == [0, FIFTEEN_MS, 2 * FIFTEEN_MS]
    assert aggregated[0]["open"] == 100 and aggregated[0]["close"] == 102
    assert [c["timestamp"] for c in aggregate_15m(small[:4] + small[5:])] == [0, 2 * FIFTEEN_MS]
    higher = [candle(i * FIFTEEN_MS, 100 + 0.1 * i) for i in range(230)]
    five = [candle(215 * FIFTEEN_MS + 5 * CANDLE_MS, 120)]
    assert trend_at_5m(five, higher) == ["LONG"]
    assert trend_at_5m(five, higher + [candle(230 * FIFTEEN_MS, 999)]) == ["LONG"]
    gap = [dict(c) for c in higher]
    for c in gap[210:]:
        c["timestamp"] += FIFTEEN_MS
    assert trend_at_5m(five, gap) == [None]
    assert trend_at_5m([candle(250 * FIFTEEN_MS, 120)], higher) == [None]
    flat = [candle(i * FIFTEEN_MS, 100) for i in range(230)]
    assert trend_at_5m(five, flat) == ["FLAT"]
    assert ema_series(higher, 200)[199] is not None

    bars = [candle(0, 99, high=99.5, low=98.5),
            candle(CANDLE_MS, 100.4, op=99, high=100.5, low=98.5),
            candle(2 * CANDLE_MS, 102, op=100.4, high=105, low=100)]
    e20, e50 = [100, 100, 100], [99.5, 99.5, 99.5]
    values = [{"atr": 1.0}] * 3
    trend = ["LONG"] * 3
    assert setup_side(bars, 1, e20, e50, values, trend) == "LONG"
    result = run_window(bars, values, e20, e50, trend, 0, 2 * CANDLE_MS)
    assert result["count"] == 1
    trade = result["trades"][0]
    assert trade["entry_ts"] == 2 * CANDLE_MS  # next-bar open
    assert trade["reason"] == "TARGET" and trade["entry_fee"] > 0
    assert trade["exit_fee"] > 0 and trade["net"] < trade["gross"]
    p = open_trade("LONG", 100.4, 1.0, 10000, 2 * CANDLE_MS)
    assert exit_hit(p, {**bars[2], "low": 98.0})[1] == "STOP"
    print("V10 SELFTEST ERFOLGREICH: 15m close, gap, future isolation, next-open, stop-first, fees")


if __name__ == "__main__":
    main()
