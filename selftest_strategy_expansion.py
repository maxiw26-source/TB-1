from strategy_breakout_v2 import build_retest_plan, detect_breakout


def candle(ts, o, h, l, c):
    return {
        "timestamp": ts,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1.0,
    }


base = []
for i in range(21):
    base.append(candle(i, 100.0, 101.0, 99.0, 100.0))

base[-1] = candle(21, 100.0, 101.2, 99.9, 101.1)
breakout = detect_breakout(base, "LONG")
assert breakout is not None
assert breakout["side"] == "LONG"

pending = {
    "side": "LONG",
    "level": 101.0,
    "breakout_timestamp": 21,
}

retest_candle = candle(22, 101.00, 101.40, 100.95, 101.20)
plan = build_retest_plan(retest_candle, pending, 0.50)
assert plan is not None
assert plan["entry"] > plan["stop"]
assert plan["tp1"] > plan["entry"]
assert plan["tp2"] > plan["tp1"]

short_pending = {
    "side": "SHORT",
    "level": 100.0,
    "breakout_timestamp": 30,
}
short_retest = candle(31, 99.95, 100.05, 99.40, 99.60)
short_plan = build_retest_plan(short_retest, short_pending, 0.50)
assert short_plan is not None
assert short_plan["stop"] > short_plan["entry"]
assert short_plan["tp1"] < short_plan["entry"]
assert short_plan["tp2"] < short_plan["tp1"]

print("EXPANSION SELFTEST ERFOLGREICH")
