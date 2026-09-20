import config

EMA_TREND_PERIOD = int(getattr(config, "EMA_TREND_PERIOD", 200))
ADX_PERIOD = int(getattr(config, "ADX_PERIOD", 14))
ATR_PERIOD = int(getattr(config, "ATR_PERIOD", 14))

BREAKOUT_LOOKBACK = 20
BREAKOUT_BUFFER_PERCENT = 0.05
RETEST_TOLERANCE_PERCENT = 0.12
RETEST_CONFIRM_PERCENT = 0.03
RETEST_EXPIRY_CANDLES = 10
ADX_MINIMUM_BREAKOUT = 18.0
STOP_BUFFER_ATR = 0.10


def ema(values, period):
    if len(values) < period:
        return None
    multiplier = 2.0 / (period + 1.0)
    value = sum(values[:period]) / period
    for price in values[period:]:
        value = price * multiplier + value * (1.0 - multiplier)
    return value


def atr(candles, period):
    if len(candles) < period + 1:
        return None
    tr_values = []
    for i in range(1, len(candles)):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        previous_close = float(candles[i - 1]["close"])
        tr_values.append(
            max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )
    value = sum(tr_values[:period]) / period
    for current in tr_values[period:]:
        value = (value * (period - 1) + current) / period
    return value


def adx(candles, period):
    if len(candles) < period * 2 + 1:
        return None

    tr_values = []
    plus_values = []
    minus_values = []

    for i in range(1, len(candles)):
        current = candles[i]
        previous = candles[i - 1]

        high = float(current["high"])
        low = float(current["low"])
        previous_high = float(previous["high"])
        previous_low = float(previous["low"])
        previous_close = float(previous["close"])

        up = high - previous_high
        down = previous_low - low

        plus_values.append(up if up > down and up > 0 else 0.0)
        minus_values.append(down if down > up and down > 0 else 0.0)
        tr_values.append(
            max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )

    smoothed_tr = sum(tr_values[:period])
    smoothed_plus = sum(plus_values[:period])
    smoothed_minus = sum(minus_values[:period])

    dx_values = []

    for i in range(period, len(tr_values)):
        if i > period:
            smoothed_tr = smoothed_tr - smoothed_tr / period + tr_values[i]
            smoothed_plus = smoothed_plus - smoothed_plus / period + plus_values[i]
            smoothed_minus = smoothed_minus - smoothed_minus / period + minus_values[i]

        if smoothed_tr <= 0:
            dx_values.append(0.0)
            continue

        plus_di = 100.0 * smoothed_plus / smoothed_tr
        minus_di = 100.0 * smoothed_minus / smoothed_tr
        denominator = plus_di + minus_di
        dx_values.append(
            0.0
            if denominator <= 0
            else 100.0 * abs(plus_di - minus_di) / denominator
        )

    if len(dx_values) < period:
        return None

    value = sum(dx_values[:period]) / period
    for current in dx_values[period:]:
        value = (value * (period - 1) + current) / period
    return value


def detect_breakout(entry_candles, side, lookback=BREAKOUT_LOOKBACK):
    if len(entry_candles) < lookback + 1:
        return None

    current = entry_candles[-1]
    recent = entry_candles[-lookback - 1:-1]

    if side == "LONG":
        level = max(float(c["high"]) for c in recent)
        trigger = level * (1.0 + BREAKOUT_BUFFER_PERCENT / 100.0)
        if float(current["close"]) > trigger:
            return {
                "side": "LONG",
                "level": level,
                "breakout_timestamp": int(current["timestamp"]),
            }
    else:
        level = min(float(c["low"]) for c in recent)
        trigger = level * (1.0 - BREAKOUT_BUFFER_PERCENT / 100.0)
        if float(current["close"]) < trigger:
            return {
                "side": "SHORT",
                "level": level,
                "breakout_timestamp": int(current["timestamp"]),
            }

    return None


def retest_confirmed(candle, pending):
    side = pending["side"]
    level = float(pending["level"])
    tolerance = level * RETEST_TOLERANCE_PERCENT / 100.0
    confirm = level * RETEST_CONFIRM_PERCENT / 100.0

    open_price = float(candle["open"])
    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])

    if side == "LONG":
        return (
            low <= level + tolerance
            and close > level + confirm
            and close > open_price
        )

    return (
        high >= level - tolerance
        and close < level - confirm
        and close < open_price
    )


def build_retest_plan(candle, pending, atr_value):
    if not retest_confirmed(candle, pending):
        return None

    side = pending["side"]
    level = float(pending["level"])
    entry = float(candle["close"])

    if side == "LONG":
        stop = min(float(candle["low"]), level) - atr_value * STOP_BUFFER_ATR
        risk = entry - stop
        if risk <= 0:
            return None
        tp1 = entry + risk * 0.8
        tp2 = entry + risk * 1.5
    else:
        stop = max(float(candle["high"]), level) + atr_value * STOP_BUFFER_ATR
        risk = stop - entry
        if risk <= 0:
            return None
        tp1 = entry - risk * 0.8
        tp2 = entry - risk * 1.5

    return {
        "side": side,
        "entry": entry,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "risk_distance": risk,
        "breakout_level": level,
        "breakout_timestamp": int(pending["breakout_timestamp"]),
        "entry_timestamp": int(candle["timestamp"]),
    }


def calculate_breakout_candidate(
    entry_candles,
    confirmation_candles,
    trend_candles,
):
    if len(entry_candles) < BREAKOUT_LOOKBACK + 2:
        return {"signal": None, "reason": "Zu wenige 1m-Kerzen", "indicators": {}}
    if len(confirmation_candles) < ADX_PERIOD * 2 + 2:
        return {"signal": None, "reason": "Zu wenige 5m-Kerzen", "indicators": {}}
    if len(trend_candles) < EMA_TREND_PERIOD:
        return {"signal": None, "reason": "Zu wenige 15m-Kerzen", "indicators": {}}

    trend_close = float(trend_candles[-1]["close"])
    trend_ema = ema(
        [float(c["close"]) for c in trend_candles],
        EMA_TREND_PERIOD,
    )
    confirmation_adx = adx(confirmation_candles, ADX_PERIOD)
    entry_atr = atr(entry_candles, ATR_PERIOD)

    if trend_ema is None or confirmation_adx is None or entry_atr is None:
        return {"signal": None, "reason": "Indikatoren nicht verfügbar", "indicators": {}}

    indicators = {
        "ema200": trend_ema,
        "adx": confirmation_adx,
        "atr": entry_atr,
    }

    if confirmation_adx < ADX_MINIMUM_BREAKOUT:
        return {
            "signal": None,
            "reason": "ADX zu niedrig",
            "indicators": indicators,
        }

    side = "LONG" if trend_close > trend_ema else "SHORT"
    breakout = detect_breakout(entry_candles, side)

    indicators["side"] = side
    indicators["breakout"] = breakout

    if breakout is None:
        return {
            "signal": None,
            "reason": f"{side}-Trend, aber kein Breakout",
            "indicators": indicators,
        }

    return {
        "signal": f"BREAKOUT_{side}",
        "reason": f"{side}-Breakout wartet auf Retest",
        "indicators": indicators,
    }
