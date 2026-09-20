import config
from strategy_breakout_v2 import adx, atr, ema

EMA_TREND_PERIOD = int(getattr(config, "EMA_TREND_PERIOD", 200))
ADX_PERIOD = int(getattr(config, "ADX_PERIOD", 14))
ATR_PERIOD = int(getattr(config, "ATR_PERIOD", 14))


def detect_breakout(entry_candles, side, params):
    lookback = int(params["lookback"])
    if len(entry_candles) < lookback + 1:
        return None

    current = entry_candles[-1]
    recent = entry_candles[-lookback - 1:-1]
    buffer_pct = float(params["breakout_buffer_percent"]) / 100.0

    if side == "LONG":
        level = max(float(c["high"]) for c in recent)
        if float(current["close"]) > level * (1.0 + buffer_pct):
            return {
                "side": "LONG",
                "level": level,
                "breakout_timestamp": int(current["timestamp"]),
            }
    else:
        level = min(float(c["low"]) for c in recent)
        if float(current["close"]) < level * (1.0 - buffer_pct):
            return {
                "side": "SHORT",
                "level": level,
                "breakout_timestamp": int(current["timestamp"]),
            }

    return None


def retest_plan(candle, pending, atr_value, params):
    side = pending["side"]
    level = float(pending["level"])
    tolerance = level * float(params["retest_tolerance_percent"]) / 100.0
    confirm = level * float(params["retest_confirm_percent"]) / 100.0
    stop_buffer = atr_value * float(params["stop_buffer_atr"])

    o = float(candle["open"])
    h = float(candle["high"])
    l = float(candle["low"])
    c = float(candle["close"])

    if side == "LONG":
        ok = l <= level + tolerance and c > level + confirm and c > o
        if not ok:
            return None
        stop = min(l, level) - stop_buffer
        risk = c - stop
        if risk <= 0:
            return None
        tp1 = c + risk * float(params["tp1_r"])
        tp2 = c + risk * float(params["tp2_r"])
    else:
        ok = h >= level - tolerance and c < level - confirm and c < o
        if not ok:
            return None
        stop = max(h, level) + stop_buffer
        risk = stop - c
        if risk <= 0:
            return None
        tp1 = c - risk * float(params["tp1_r"])
        tp2 = c - risk * float(params["tp2_r"])

    return {
        "side": side,
        "entry": c,
        "stop": stop,
        "tp1": tp1,
        "tp2": tp2,
        "entry_timestamp": int(candle["timestamp"]),
        "breakout_timestamp": int(pending["breakout_timestamp"]),
    }


def calculate_candidate(entry_candles, confirmation_candles, trend_candles, params):
    lookback = int(params["lookback"])

    if len(entry_candles) < lookback + 2:
        return None
    if len(confirmation_candles) < ADX_PERIOD * 2 + 2:
        return None
    if len(trend_candles) < EMA_TREND_PERIOD:
        return None

    trend_close = float(trend_candles[-1]["close"])
    trend_ema = ema(
        [float(c["close"]) for c in trend_candles],
        EMA_TREND_PERIOD,
    )
    confirmation_adx = adx(confirmation_candles, ADX_PERIOD)

    if trend_ema is None or confirmation_adx is None:
        return None

    if confirmation_adx < float(params["adx_minimum"]):
        return None

    side = "LONG" if trend_close > trend_ema else "SHORT"
    return detect_breakout(entry_candles, side, params)
