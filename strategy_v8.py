import config

EMA_TREND_PERIOD = int(getattr(config, "EMA_TREND_PERIOD", 200))
ADX_PERIOD = int(getattr(config, "ADX_PERIOD", 14))
ATR_PERIOD = int(getattr(config, "ATR_PERIOD", 14))


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
        values.append(
            max(
                high - low,
                abs(high - previous_close),
                abs(low - previous_close),
            )
        )

    value = sum(values[:period]) / period
    for current in values[period:]:
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


def sweep(candles, index, atr_value, side, params):
    swing_lookback = int(params["swing_lookback"])
    sweep_buffer_atr = float(params["sweep_buffer_atr"])

    if index < swing_lookback:
        return None

    current = candles[index]

    if side == "LONG":
        local_level = min(
            float(c["low"])
            for c in candles[index - swing_lookback:index]
        )

        ok = (
            float(current["low"]) < local_level - atr_value * sweep_buffer_atr
            and float(current["close"]) > local_level
            and float(current["close"]) > float(current["open"])
        )

        price = float(current["low"])

    else:
        local_level = max(
            float(c["high"])
            for c in candles[index - swing_lookback:index]
        )

        ok = (
            float(current["high"]) > local_level + atr_value * sweep_buffer_atr
            and float(current["close"]) < local_level
            and float(current["close"]) < float(current["open"])
        )

        price = float(current["high"])

    if not ok:
        return None

    return {
        "index": index,
        "price": price,
        "timestamp": int(current["timestamp"]),
    }


def bos(candles, index, side, params):
    lookback = int(params["bos_lookback"])

    if index < lookback:
        return False

    if side == "LONG":
        level = max(
            float(c["high"])
            for c in candles[index - lookback:index]
        )
        return float(candles[index]["close"]) > level

    level = min(
        float(c["low"])
        for c in candles[index - lookback:index]
    )
    return float(candles[index]["close"]) < level


def fvg(candles, index, atr_value, side, params):
    if index < 2:
        return None

    min_ratio = float(params["min_fvg_atr_ratio"])

    if side == "LONG":
        lower = float(candles[index - 2]["high"])
        upper = float(candles[index]["low"])
    else:
        lower = float(candles[index]["high"])
        upper = float(candles[index - 2]["low"])

    if upper - lower <= atr_value * min_ratio:
        return None

    return {
        "lower": lower,
        "upper": upper,
        "index": index,
        "timestamp": int(candles[index]["timestamp"]),
    }


def find_setup(candles, atr_value, side, params):
    last = len(candles) - 1
    swing_lookback = int(params["swing_lookback"])
    sweep_to_bos = int(params["sweep_to_bos_candles"])
    bos_to_fvg = int(params["bos_to_fvg_candles"])
    expiry = int(params["setup_expiry_candles"])

    earliest = max(
        swing_lookback,
        last - sweep_to_bos - bos_to_fvg - expiry,
    )

    for sweep_index in range(earliest, last + 1):
        sweep_info = sweep(
            candles,
            sweep_index,
            atr_value,
            side,
            params,
        )

        if sweep_info is None:
            continue

        latest_bos = min(last, sweep_index + sweep_to_bos)

        for bos_index in range(sweep_index + 1, latest_bos + 1):
            if not bos(candles, bos_index, side, params):
                continue

            latest_fvg = min(last, bos_index + bos_to_fvg)

            for fvg_index in range(bos_index, latest_fvg + 1):
                fvg_info = fvg(
                    candles,
                    fvg_index,
                    atr_value,
                    side,
                    params,
                )

                if fvg_info is None:
                    continue

                if last - fvg_index > expiry:
                    continue

                entry = (
                    fvg_info["lower"]
                    + fvg_info["upper"]
                ) / 2.0

                stop_buffer = (
                    atr_value
                    * float(params["stop_buffer_atr"])
                )

                stop = (
                    sweep_info["price"] - stop_buffer
                    if side == "LONG"
                    else sweep_info["price"] + stop_buffer
                )

                risk = (
                    entry - stop
                    if side == "LONG"
                    else stop - entry
                )

                if risk <= 0:
                    continue

                return {
                    "side": side,
                    "entry": entry,
                    "stop": stop,
                    "fvg_lower": fvg_info["lower"],
                    "fvg_upper": fvg_info["upper"],
                    "sweep_timestamp": sweep_info["timestamp"],
                    "bos_timestamp": int(candles[bos_index]["timestamp"]),
                    "fvg_timestamp": fvg_info["timestamp"],
                }

    return None


def calculate_signal(entry_candles, confirmation_candles, trend_candles, params):
    minimum_entry = max(
        ATR_PERIOD + 2,
        int(params["swing_lookback"]) + 3,
        int(params["bos_lookback"]) + 3,
    )

    if len(entry_candles) < minimum_entry:
        return {"signal": None, "reason": "Zu wenige 1m-Kerzen", "indicators": {}}

    if len(confirmation_candles) < ADX_PERIOD * 2 + 2:
        return {"signal": None, "reason": "Zu wenige 5m-Kerzen", "indicators": {}}

    if len(trend_candles) < EMA_TREND_PERIOD:
        return {"signal": None, "reason": "Zu wenige 15m-Kerzen", "indicators": {}}

    entry_atr = atr(entry_candles, ATR_PERIOD)
    confirmation_adx = adx(confirmation_candles, ADX_PERIOD)
    trend_ema = ema(
        [float(c["close"]) for c in trend_candles],
        EMA_TREND_PERIOD,
    )

    if entry_atr is None or confirmation_adx is None or trend_ema is None:
        return {"signal": None, "reason": "Indikatoren nicht verfügbar", "indicators": {}}

    trend_close = float(trend_candles[-1]["close"])

    indicators = {
        "atr": entry_atr,
        "adx": confirmation_adx,
        "ema200": trend_ema,
    }

    if confirmation_adx < float(params["adx_minimum"]):
        return {"signal": None, "reason": "ADX zu niedrig", "indicators": indicators}

    side = "LONG" if trend_close > trend_ema else "SHORT"

    setup = find_setup(
        entry_candles,
        entry_atr,
        side,
        params,
    )

    if setup is None:
        return {
            "signal": None,
            "reason": f"{side} – kein gültiges Setup",
            "indicators": indicators,
        }

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
