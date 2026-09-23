"""V9 research-only 5m mean-reversion indicators and entry rules.

All indicators at index i use data up to and including candle i.
The backtest enters only at the following candle's open.
No exchange, order, or credential code is imported.
"""
from collections import deque
from math import sqrt


PROFILE = {
    "name": "v9_mean_reversion_v1",
    "bb_period": 20,
    "bb_sigma": 2.0,
    "vwap_period": 48,  # 4 hours of 5m bars
    "rsi_period": 14,
    "rsi_long_max": 32.0,
    "rsi_short_min": 68.0,
    "adx_period": 14,
    "adx_max": 28.0,
    "atr_period": 14,
    "vwap_distance_atr": 0.5,
    "stop_atr": 1.5,
    "minimum_reward_risk": 0.8,
    "max_hold_bars": 18,
}


def indicators(candles, params=PROFILE):
    """O(n) trailing indicators; no future candles or forward-filled gaps."""
    n = len(candles)
    output = [None] * n
    bb_n = int(params["bb_period"])
    vwap_n = int(params["vwap_period"])
    rsi_n = int(params["rsi_period"])
    atr_n = int(params["atr_period"])
    adx_n = int(params["adx_period"])

    closes = deque()
    bb_sum = bb_sum_sq = 0.0
    vwap_queue = deque()
    pv_sum = volume_sum = 0.0

    tr_seed = gain_seed = loss_seed = 0.0
    plus_seed = minus_seed = 0.0
    atr = avg_gain = avg_loss = None
    sm_tr = sm_plus = sm_minus = None
    dx_seed = 0.0
    adx = None

    for i, c in enumerate(candles):
        close = float(c["close"])
        high = float(c["high"])
        low = float(c["low"])
        volume = max(0.0, float(c.get("volume") or 0.0))
        if close <= 0 or high < low or volume < 0:
            raise ValueError("Ungueltige Marktdaten")

        closes.append(close)
        bb_sum += close
        bb_sum_sq += close * close
        if len(closes) > bb_n:
            old = closes.popleft()
            bb_sum -= old
            bb_sum_sq -= old * old

        typical = (high + low + close) / 3.0
        pv = typical * volume
        vwap_queue.append((pv, volume))
        pv_sum += pv
        volume_sum += volume
        if len(vwap_queue) > vwap_n:
            old_pv, old_vol = vwap_queue.popleft()
            pv_sum -= old_pv
            volume_sum -= old_vol

        if i:
            prev = candles[i - 1]
            prev_close = float(prev["close"])
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            up = high - float(prev["high"])
            down = float(prev["low"]) - low
            plus = up if up > down and up > 0 else 0.0
            minus = down if down > up and down > 0 else 0.0
            delta = close - prev_close
            gain = max(0.0, delta)
            loss = max(0.0, -delta)

            if i <= atr_n:
                tr_seed += tr
                if i == atr_n:
                    atr = tr_seed / atr_n
            else:
                atr = (atr * (atr_n - 1) + tr) / atr_n

            if i <= rsi_n:
                gain_seed += gain
                loss_seed += loss
                if i == rsi_n:
                    avg_gain = gain_seed / rsi_n
                    avg_loss = loss_seed / rsi_n
            else:
                avg_gain = (avg_gain * (rsi_n - 1) + gain) / rsi_n
                avg_loss = (avg_loss * (rsi_n - 1) + loss) / rsi_n

            if i <= adx_n:
                plus_seed += plus
                minus_seed += minus
                if i == adx_n:
                    sm_tr = tr_seed if adx_n == atr_n else sum(
                        max(
                            float(candles[j]["high"]) - float(candles[j]["low"]),
                            abs(float(candles[j]["high"]) - float(candles[j - 1]["close"])),
                            abs(float(candles[j]["low"]) - float(candles[j - 1]["close"])),
                        )
                        for j in range(1, adx_n + 1)
                    )
                    sm_plus = plus_seed
                    sm_minus = minus_seed
            else:
                sm_tr = sm_tr - sm_tr / adx_n + tr
                sm_plus = sm_plus - sm_plus / adx_n + plus
                sm_minus = sm_minus - sm_minus / adx_n + minus

            if i >= adx_n and sm_tr > 0:
                di_plus = 100 * sm_plus / sm_tr
                di_minus = 100 * sm_minus / sm_tr
                denom = di_plus + di_minus
                dx = 100 * abs(di_plus - di_minus) / denom if denom else 0.0
                if i < 2 * adx_n:
                    dx_seed += dx
                elif i == 2 * adx_n:
                    dx_seed += dx
                    adx = dx_seed / (adx_n + 1)
                else:
                    adx = (adx * (adx_n - 1) + dx) / adx_n

        if len(closes) < bb_n or len(vwap_queue) < vwap_n:
            continue
        if atr is None or avg_gain is None or adx is None or volume_sum <= 0:
            continue

        mean = bb_sum / bb_n
        variance = max(0.0, bb_sum_sq / bb_n - mean * mean)
        deviation = sqrt(variance)
        rsi = (
            100.0 * avg_gain / (avg_gain + avg_loss)
            if avg_gain + avg_loss > 0
            else 50.0
        )
        output[i] = {
            "bb_lower": mean - float(params["bb_sigma"]) * deviation,
            "bb_upper": mean + float(params["bb_sigma"]) * deviation,
            "bb_mid": mean,
            "vwap": pv_sum / volume_sum,
            "rsi": rsi,
            "adx": adx,
            "atr": atr,
        }
    return output


def signal(candle, values, params=PROFILE):
    """Signal after a CLOSED bar only, execution is next bar open."""
    if not values or values["atr"] <= 0 or values["adx"] > params["adx_max"]:
        return None
    close = float(candle["close"])
    atr = values["atr"]
    vwap = values["vwap"]
    if (
        close < values["bb_lower"]
        and values["rsi"] <= params["rsi_long_max"]
        and close < vwap - params["vwap_distance_atr"] * atr
        and (vwap - close) / (params["stop_atr"] * atr) >= params["minimum_reward_risk"]
    ):
        return "LONG"
    if (
        close > values["bb_upper"]
        and values["rsi"] >= params["rsi_short_min"]
        and close > vwap + params["vwap_distance_atr"] * atr
        and (close - vwap) / (params["stop_atr"] * atr) >= params["minimum_reward_risk"]
    ):
        return "SHORT"
    return None
