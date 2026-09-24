"""Offline checks for hourly data, no-lookahead, fills and funding."""
from backtest_v12_hourly_trend import (
    FIVE_MS, HOUR_MS, aggregate_hourly, exit_hit, funding_cost, open_trade,
    run_window, signals,
)


def candle(ts, price=100, op=None, high=None, low=None):
    return {"timestamp": ts, "open": price if op is None else op,
            "high": price + 0.05 if high is None else high,
            "low": price - 0.05 if low is None else low,
            "close": price, "volume": 10}


def main():
    five = [candle(i * FIVE_MS, 100 + i / 100) for i in range(24)]
    assert len(aggregate_hourly(five)) == 2
    assert len(aggregate_hourly(five[:4] + five[5:])) == 1
    hours = [candle(i * HOUR_MS, 100 + i * 0.1) for i in range(250)]
    baseline = signals(hours)
    altered = [dict(c) for c in hours]
    altered[-1]["close"] = 900
    assert baseline[:-1] == signals(altered)[:-1]
    broken = [dict(c) for c in hours]
    broken[230]["timestamp"] += HOUR_MS // 2
    assert signals(broken)[231] is None

    bars = [candle(0, 100), candle(HOUR_MS, 104, op=101,
                                  high=108, low=100.8)]
    result = run_window(bars, [("LONG", 1.0), None], 0, HOUR_MS)
    assert result["count"] == 1
    trade = result["trades"][0]
    assert trade["entry_ts"] == HOUR_MS and trade["reason"] == "TARGET"
    assert trade["entry_fee"] > 0 and trade["exit_fee"] > 0
    assert trade["net"] < trade["gross"]
    pos = open_trade("LONG", 101, 1.0, 10000, HOUR_MS)
    assert exit_hit(pos, {**bars[1], "low": 98})[1] == "STOP"
    assert funding_cost(pos, HOUR_MS) == 0
    assert funding_cost(pos, 8 * HOUR_MS) > 0
    print("V12 SELFTEST ERFOLGREICH: complete 1h, gap, no future data, "
          "next open, stop first, fees, funding")


if __name__ == "__main__":
    main()
