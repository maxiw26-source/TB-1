import config

EMA_TREND_PERIOD = int(getattr(config, "EMA_TREND_PERIOD", 200))
ADX_PERIOD = int(getattr(config, "ADX_PERIOD", 14))
ADX_MINIMUM = float(getattr(config, "ADX_MINIMUM", 20))
ATR_PERIOD = int(getattr(config, "ATR_PERIOD", 14))
SWING_LOOKBACK = int(getattr(config, "SWING_LOOKBACK", 10))
BOS_LOOKBACK = int(getattr(config, "BOS_LOOKBACK", 10))
SWEEP_TO_BOS_CANDLES = int(getattr(config, "SWEEP_TO_BOS_CANDLES", 8))
BOS_TO_FVG_CANDLES = int(getattr(config, "BOS_TO_FVG_CANDLES", 4))
SETUP_EXPIRY_CANDLES = int(getattr(config, "SETUP_EXPIRY_CANDLES", 5))
MIN_FVG_ATR_RATIO = float(getattr(config, "MIN_FVG_ATR_RATIO", 0.10))
SWEEP_BUFFER_ATR = float(getattr(config, "SWEEP_BUFFER_ATR", 0.05))
STOP_BUFFER_ATR = float(getattr(config, "STOP_BUFFER_ATR", 0.10))


def ema(values, period):
    if len(values) < period:
        return None
    k = 2.0 / (period + 1.0)
    value = sum(values[:period]) / period
    for current in values[period:]:
        value = current * k + value * (1.0 - k)
    return value


def atr(candles, period):
    if len(candles) < period + 1:
        return None
    values = []
    for i in range(1, len(candles)):
        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        previous_close = float(candles[i - 1]["close"])
        values.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))
    value = sum(values[:period]) / period
    for current in values[period:]:
        value = (value * (period - 1) + current) / period
    return value


def adx(candles, period):
    if len(candles) < period * 2 + 1:
        return None
    tr_values, plus_values, minus_values = [], [], []
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
        tr_values.append(max(high - low, abs(high - previous_close), abs(low - previous_close)))

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
        dx_values.append(0.0 if denominator <= 0 else 100.0 * abs(plus_di - minus_di) / denominator)

    if len(dx_values) < period:
        return None
    value = sum(dx_values[:period]) / period
    for current in dx_values[period:]:
        value = (value * (period - 1) + current) / period
    return value


def bullish_sweep(candles, index, atr_value):
    if index < SWING_LOOKBACK:
        return None
    current = candles[index]
    local_low = min(float(c["low"]) for c in candles[index - SWING_LOOKBACK:index])
    if (
        float(current["low"]) < local_low - atr_value * SWEEP_BUFFER_ATR
        and float(current["close"]) > local_low
        and float(current["close"]) > float(current["open"])
    ):
        return {"index": index, "price": float(current["low"]), "timestamp": int(current["timestamp"])}
    return None


def bearish_sweep(candles, index, atr_value):
    if index < SWING_LOOKBACK:
        return None
    current = candles[index]
    local_high = max(float(c["high"]) for c in candles[index - SWING_LOOKBACK:index])
    if (
        float(current["high"]) > local_high + atr_value * SWEEP_BUFFER_ATR
        and float(current["close"]) < local_high
        and float(current["close"]) < float(current["open"])
    ):
        return {"index": index, "price": float(current["high"]), "timestamp": int(current["timestamp"])}
    return None


def bullish_bos(candles, index):
    if index < BOS_LOOKBACK:
        return False
    level = max(float(c["high"]) for c in candles[index - BOS_LOOKBACK:index])
    return float(candles[index]["close"]) > level


def bearish_bos(candles, index):
    if index < BOS_LOOKBACK:
        return False
    level = min(float(c["low"]) for c in candles[index - BOS_LOOKBACK:index])
    return float(candles[index]["close"]) < level


def bullish_fvg(candles, index, atr_value, min_fvg_atr_ratio):
    if index < 2:
        return None
    lower = float(candles[index - 2]["high"])
    upper = float(candles[index]["low"])
    if upper - lower <= atr_value * min_fvg_atr_ratio:
        return None
    return {"lower": lower, "upper": upper, "index": index, "timestamp": int(candles[index]["timestamp"])}


def bearish_fvg(candles, index, atr_value, min_fvg_atr_ratio):
    if index < 2:
        return None
    lower = float(candles[index]["high"])
    upper = float(candles[index - 2]["low"])
    if upper - lower <= atr_value * min_fvg_atr_ratio:
        return None
    return {"lower": lower, "upper": upper, "index": index, "timestamp": int(candles[index]["timestamp"])}


def find_setup(
    candles,
    atr_value,
    side,
    swing_lookback,
    sweep_to_bos_candles,
    bos_to_fvg_candles,
    min_fvg_atr_ratio,
):
    last = len(candles) - 1
    earliest = max(
        swing_lookback,
        last - sweep_to_bos_candles - bos_to_fvg_candles - SETUP_EXPIRY_CANDLES,
    )

    for sweep_index in range(earliest, last + 1):
        sweep = (
            bullish_sweep(candles, sweep_index, atr_value)
            if side == "LONG"
            else bearish_sweep(candles, sweep_index, atr_value)
        )
        if sweep is None:
            continue

        latest_bos = min(last, sweep_index + SWEEP_TO_BOS_CANDLES)

        for bos_index in range(sweep_index + 1, latest_bos + 1):
            bos_ok = bullish_bos(candles, bos_index) if side == "LONG" else bearish_bos(candles, bos_index)
            if not bos_ok:
                continue

            latest_fvg = min(last, bos_index + BOS_TO_FVG_CANDLES)

            for fvg_index in range(bos_index, latest_fvg + 1):
                fvg = (
    bullish_fvg(
        candles,
        fvg_index,
        atr_value,
        min_fvg_atr_ratio,
    )
    if side == "LONG"
    else bearish_fvg(
        candles,
        fvg_index,
        atr_value,
        min_fvg_atr_ratio,
    )
)
                if fvg is None or last - fvg_index > SETUP_EXPIRY_CANDLES:
                    continue

                entry = (fvg["lower"] + fvg["upper"]) / 2.0
                stop = (
                    sweep["price"] - atr_value * STOP_BUFFER_ATR
                    if side == "LONG"
                    else sweep["price"] + atr_value * STOP_BUFFER_ATR
                )
                risk = entry - stop if side == "LONG" else stop - entry
                if risk <= 0:
                    continue

                return {
                    "side": side,
                    "entry": entry,
                    "stop": stop,
                    "fvg_lower": fvg["lower"],
                    "fvg_upper": fvg["upper"],
                    "sweep_timestamp": sweep["timestamp"],
                    "bos_timestamp": int(candles[bos_index]["timestamp"]),
                    "fvg_timestamp": fvg["timestamp"],
                }

    return None


def calculate_signal(
    entry_candles,
    confirmation_candles,
    trend_candles,
    swing_lookback,
    sweep_to_bos_candles,
    bos_to_fvg_candles,
    min_fvg_atr_ratio,
):

    if len(entry_candles) < max(ATR_PERIOD + 2, swing_lookback + 3, BOS_LOOKBACK + 3):
        return {"signal": None, "reason": "Zu wenige 1m-Kerzen", "indicators": {}}
    if len(confirmation_candles) < ADX_PERIOD * 2 + 2:
        return {"signal": None, "reason": "Zu wenige 5m-Kerzen", "indicators": {}}
    if len(trend_candles) < EMA_TREND_PERIOD:
        return {"signal": None, "reason": "Zu wenige 15m-Kerzen", "indicators": {}}

    entry_atr = atr(entry_candles, ATR_PERIOD)
    confirmation_adx = adx(confirmation_candles, ADX_PERIOD)
    trend_ema = ema([float(c["close"]) for c in trend_candles], EMA_TREND_PERIOD)

    if entry_atr is None or confirmation_adx is None or trend_ema is None:
        return {"signal": None, "reason": "Indikatoren nicht verfügbar", "indicators": {}}

    trend_close = float(trend_candles[-1]["close"])
    indicators = {
        "atr": entry_atr,
        "adx": confirmation_adx,
        "ema200": trend_ema,
        "bullish_trend": trend_close > trend_ema,
        "bearish_trend": trend_close < trend_ema,
    }

    if confirmation_adx < ADX_MINIMUM:
        return {"signal": None, "reason": "ADX zu niedrig", "indicators": indicators}

    side = "LONG" if trend_close > trend_ema else "SHORT"
    setup = find_setup(
    entry_candles,
    entry_atr,
    side,
    swing_lookback,
    sweep_to_bos_candles,
    bos_to_fvg_candles,
    min_fvg_atr_ratio,
)

    if setup is None:
        return {"signal": None, "reason": f"{side} – kein gültiges Setup", "indicators": indicators}

    setup_id = (
        f"{side}-{setup['sweep_timestamp']}-"
        f"{setup['bos_timestamp']}-"
        f"{setup['fvg_timestamp']}"
    )

    indicators["setup"] = setup
    indicators["setup_id"] = setup_id

    return {
        "signal": f"PENDING_{side}",
        "reason": f"{side}-Setup wartet auf FVG-Retracement",
        "indicators": indicators,
    }