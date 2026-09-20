from strategy_v8 import fvg, sweep


def candle(ts, o, h, l, c):
    return {
        "timestamp": ts,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1.0,
    }


params = {
    "swing_lookback": 3,
    "bos_lookback": 3,
    "sweep_to_bos_candles": 4,
    "bos_to_fvg_candles": 3,
    "setup_expiry_candles": 5,
    "min_fvg_atr_ratio": 0.05,
    "sweep_buffer_atr": 0.05,
    "stop_buffer_atr": 0.10,
    "adx_minimum": 18.0,
}

candles = [
    candle(1, 100, 101, 99, 100),
    candle(2, 100, 101, 99.2, 100),
    candle(3, 100, 101, 99.1, 100),
    candle(4, 99.5, 100.2, 98.5, 99.8),
]

assert sweep(candles, 3, 1.0, "LONG", params) is not None

fvg_candles = [
    candle(1, 100, 100.2, 99.8, 100.0),
    candle(2, 100.1, 100.3, 100.0, 100.2),
    candle(3, 100.5, 100.8, 100.5, 100.7),
]

assert fvg(fvg_candles, 2, 1.0, "LONG", params) is not None

print("V8 SELFTEST ERFOLGREICH")
