"""Checks 15m candle closing boundaries and future-data isolation."""
from backtest_v9_adx15m import entry_mask


def candle(ts, price=100.0):
    return {"timestamp": ts, "open": price, "high": price + 1,
            "low": price - 1, "close": price, "volume": 10.0}


def main():
    m = 60_000
    fifteen = [candle(i * 15 * m) for i in range(80)]
    five = [candle(i * 5 * m) for i in range(240)]
    baseline = entry_mask(five, fifteen)
    assert baseline[3 * 55 + 1]  # lateral ADX 0 with a fully closed candle
    assert baseline[3 * 55]  # boundary is usable at bar close
    assert baseline[3 * 79 + 2]

    # The current 15m candle can change arbitrarily without affecting a
    # signal at 12:10 that is evaluated at 12:15.
    before = entry_mask([candle(12 * 60 * m + 10 * m)], fifteen)
    future = fifteen + [candle(80 * 15 * m, price=200)]
    assert entry_mask(five, future) == baseline
    assert entry_mask([candle(12 * 60 * m + 10 * m)], future) == before

    # At 12:15 the 12:00 candle is closed; a 12:05 signal cannot use it.
    first = [candle(i * 15 * m) for i in range(48)]
    assert not entry_mask([candle(47 * 15 * m + 5 * m)], first)[0]
    assert entry_mask([candle(47 * 15 * m + 10 * m)], first)[0]

    gap = [dict(c) for c in fifteen]
    for c in gap[60:]:
        c["timestamp"] += 15 * m
    assert not entry_mask([candle(61 * 15 * m + 10 * m)], gap)[0]
    assert not entry_mask([candle(90 * 15 * m)], fifteen)[0]
    trend = [candle(i * 15 * m, price=100 + i) for i in range(80)]
    assert not entry_mask(five, trend)[-1]
    print("V9.1 ADX15M SELFTEST ERFOLGREICH: candle close, gap, staleness, future isolation")


if __name__ == "__main__":
    main()
