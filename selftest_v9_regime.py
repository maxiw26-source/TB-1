"""Offline checks for V9 regime masking and look-ahead safety."""
from backtest_v9 import run_window
from backtest_v9_regime import entry_mask
from strategy_v9 import indicators


def candle(i, close=100.0, high=None, low=None):
    return {
        "timestamp": i * 300_000,
        "open": close,
        "high": close + 0.5 if high is None else high,
        "low": close - 0.5 if low is None else low,
        "close": close,
        "volume": 10.0,
    }


def main():
    prices = [100 + (i % 12) * 0.1 for i in range(100)]
    candles = [candle(i, v) for i, v in enumerate(prices)]
    features = indicators(candles)
    original = entry_mask(candles, features, max_drift_atr=2.0)
    assert len(original) == len(candles)
    assert not any(original[:48])
    assert any(original[48:])

    future_candle = candle(100, close=160)
    extended = candles + [future_candle]
    future_features = indicators(extended)
    later = entry_mask(extended, future_features, max_drift_atr=2.0)
    assert original == later[:-1], "Future candle changed earlier regime gate"

    gapped = [dict(c) for c in candles]
    for i in range(61, len(gapped)):
        gapped[i]["timestamp"] += 900_000
    gapped_features = indicators(gapped)
    gapped_mask = entry_mask(gapped, gapped_features, 2.0)
    assert not gapped_mask[65], "Discontinuous 4h lookback must fail closed"

    buy = {
        "bb_lower": 100.0, "bb_upper": 108.0,
        "rsi": 20.0, "adx": 10.0,
        "atr": 1.0, "vwap": 103.0,
    }
    tiny = [
        {
            **candle(0, 99.0, high=99.8, low=98.5),
            "open": 99.0,
        },
        {
            **candle(1, 100.0, high=105, low=96),
            "open": 99.5,
        },
        candle(2, 101.0),
    ]
    test_features = [buy, None, None]
    baseline = run_window(tiny, test_features, 0, 600_000)
    allowed = run_window(
        tiny, test_features, 0, 600_000, entry_allowed=[True] * 3
    )
    rejected = run_window(
        tiny, test_features, 0, 600_000, entry_allowed=[False] * 3
    )
    assert baseline["count"] == allowed["count"] == 1
    assert baseline["net"] == allowed["net"]
    assert rejected["count"] == 0
    print("V9 REGIME SELFTEST ERFOLGREICH")
    print("Vergangenheitsdaten, Gap-Schutz, Baseline-Paritaet, Entry-Gate: OK")
    print("Keine echten Orders oder API-Verbindung.")


if __name__ == "__main__":
    main()
