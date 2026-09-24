"""Offline checks for UTC range completeness, timing, gaps and costs."""
from backtest_v11_opening_range import (
    CANDLE_MS, DAY_MS, exit_hit, open_trade, opening_ranges, run_window, signal_at,
)


def bar(ts, close=100, high=100.5, low=99.5, volume=10, op=100):
    return {"timestamp": ts, "open": op, "high": high, "low": low,
            "close": close, "volume": volume}


def main():
    # Previous-day bars supply volume history; current day 00:00–00:55 is complete.
    previous = [bar(DAY_MS - (20 - i) * CANDLE_MS) for i in range(20)]
    morning = [bar(DAY_MS + i * CANDLE_MS) for i in range(12)]
    breakout = bar(DAY_MS + 12 * CANDLE_MS, 101, 101.2, 100,
                   20, 100)
    entry = bar(DAY_MS + 13 * CANDLE_MS, 103, 104.5, 100.5,
                10, 101.1)
    candles = previous + morning + [breakout, entry]
    values = [{"atr": 1.0}] * len(candles)
    ranges = opening_ranges(candles)
    assert ranges[1] == (100.5, 99.5)
    assert 1 not in opening_ranges(previous + morning[:5] + morning[6:])
    assert signal_at(candles, len(candles) - 2, values, ranges) == ("LONG", 1.0)
    assert signal_at(candles[:-1] + [bar(entry["timestamp"], close=500)],
                     len(candles) - 2, values, ranges) == ("LONG", 1.0)
    result = run_window(candles, values, ranges, DAY_MS, entry["timestamp"])
    assert result["count"] == 1
    trade = result["trades"][0]
    assert trade["entry_ts"] == entry["timestamp"]
    assert trade["reason"] == "TARGET" and trade["net"] < trade["gross"]
    assert trade["entry_fee"] > 0 and trade["exit_fee"] > 0
    position = open_trade("LONG", 101.1, 1.0, 10000, entry["timestamp"])
    assert exit_hit(position, {**entry, "low": 98})[1] == "STOP"
    gap = [dict(c) for c in candles]
    gap[12]["timestamp"] += CANDLE_MS // 2
    assert signal_at(gap, len(gap) - 2, values, opening_ranges(gap)) is None
    print("V11 SELFTEST ERFOLGREICH: UTC range, next open, future isolation, "
          "gap, stop first, fees")


if __name__ == "__main__":
    main()
