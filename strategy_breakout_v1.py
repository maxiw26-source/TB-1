import config


EMA_TREND_PERIOD = int(
    getattr(config, "EMA_TREND_PERIOD", 200)
)


def ema(values, period):
    if len(values) < period:
        return None

    multiplier = 2 / (period + 1)
    value = sum(values[:period]) / period

    for price in values[period:]:
        value = (
            price * multiplier
            + value * (1 - multiplier)
        )

    return value


def find_breakout(candles, side, lookback=20):
    if len(candles) < lookback + 2:
        return None

    recent = candles[-lookback - 1:-1]
    current = candles[-1]

    if side == "LONG":
        level = max(
            float(candle["high"])
            for candle in recent
        )

        if float(current["close"]) > level * 1.0010:
            return {
                "side": "LONG",
                "level": level,
                "breakout_timestamp": current["timestamp"],
            }

    else:
        level = min(
            float(candle["low"])
            for candle in recent
        )

        if float(current["close"]) < level * 0.9990:
            return {
                "side": "SHORT",
                "level": level,
                "breakout_timestamp": current["timestamp"],
            }

    return None

def find_retest(candles, breakout, tolerance_percent=0.10):
    if len(candles) < 2:
        return None

    current = candles[-1]
    level = float(breakout["level"])
    tolerance = level * tolerance_percent / 100

    low = float(current["low"])
    high = float(current["high"])
    close = float(current["close"])

    if breakout["side"] == "LONG":
        touched = low <= level + tolerance
        confirmed = close > level * 1.0005

        if touched and confirmed:
            return {
                "side": "LONG",
                "level": level,
                "retest_timestamp": current["timestamp"],
            }

    else:
        touched = high >= level - tolerance
        confirmed = close < level * 0.9995

        if touched and confirmed:
            return {
                "side": "SHORT",
                "level": level,
                "retest_timestamp": current["timestamp"],
            }

    return None

def calculate_breakout_signal(
    entry_candles,
    confirmation_candles,
    trend_candles,
):
    if len(trend_candles) < EMA_TREND_PERIOD:
        return {
            "signal": None,
            "reason": "Zu wenige 15m-Kerzen",
            "indicators": {},
        }

    trend_closes = [
        float(candle["close"])
        for candle in trend_candles
    ]

    trend_ema = ema(
        trend_closes,
        EMA_TREND_PERIOD,
    )

    trend_close = trend_closes[-1]

    side = (
        "LONG"
        if trend_close > trend_ema
        else "SHORT"
    )

    breakout = find_breakout(
        entry_candles,
        side,
        lookback=20,
    )

    if breakout is None:
        return {
            "signal": None,
            "reason": f"{side}-Trend, aber kein Breakout",
            "indicators": {
                "trend_close": trend_close,
                "trend_ema": trend_ema,
                "side": side,
            },
        }

    retest = find_retest(
        entry_candles,
        breakout,
    )

    if retest is None:
        return {
            "signal": None,
            "reason": f"{side}-Breakout erkannt, wartet auf Retest",
            "indicators": {
                "trend_close": trend_close,
                "trend_ema": trend_ema,
                "side": side,
                "breakout": breakout,
            },
        }

    entry = float(
        entry_candles[-1]["close"]
    )

    if side == "LONG":
        stop = min(
            float(entry_candles[-1]["low"]),
            float(retest["level"]),
        )
    else:
        stop = max(
            float(entry_candles[-1]["high"]),
            float(retest["level"]),
        )

    if side == "LONG":
        risk_distance = entry - stop
    else:
        risk_distance = stop - entry

    if risk_distance <= 0:
        return {
            "signal": None,
            "reason": "Ungültiger Stop-Abstand",
            "indicators": {},
        }

    if side == "LONG":
        tp1 = entry + risk_distance * 0.8
        tp2 = entry + risk_distance * 1.5
    else:
        tp1 = entry - risk_distance * 0.8
        tp2 = entry - risk_distance * 1.5

    return {
        "signal": f"RETEST_{side}",
        "reason": f"{side}-Breakout mit Retest bestätigt",
        "indicators": {
            "trend_close": trend_close,
            "trend_ema": trend_ema,
            "side": side,
            "breakout": breakout,
            "retest": retest,
            "entry": entry,
            "stop": stop,
            "tp1": tp1,
            "tp2": tp2,
        },
    }