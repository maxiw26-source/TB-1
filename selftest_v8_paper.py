import live_paper_v8_btc as v8


def candle(ts, o, h, l, c):
    return {
        "timestamp": ts,
        "open": o,
        "high": h,
        "low": l,
        "close": c,
        "volume": 1.0,
    }


long_setup = {
    "side": "LONG",
    "entry": 100.0,
    "stop": 98.0,
}

position = v8.build_position(long_setup)

assert position["side"] == "LONG"
assert round(position["tp1"], 2) == 102.0
assert round(position["tp2"], 2) == 102.6

event = v8.check_position(
    position,
    candle(1, 100, 102.1, 99.9, 102.0),
)

assert event == "TP1"
assert position["tp1_hit"] is True
assert round(position["stop"], 2) == 100.0

event = v8.check_position(
    position,
    candle(2, 102, 102.7, 101.9, 102.6),
)

assert event == "TP2"
assert position["remaining"] == 0.0

short_setup = {
    "side": "SHORT",
    "entry": 100.0,
    "stop": 102.0,
}

short_position = v8.build_position(short_setup)

event = v8.check_position(
    short_position,
    candle(3, 100, 100.1, 97.9, 98.0),
)

assert event == "TP1"
assert short_position["tp1_hit"] is True
assert round(short_position["stop"], 2) == 100.0

print("V8 PAPER MONITOR SELFTEST ERFOLGREICH")
print("Keine echte Order-Funktion wurde aufgerufen.")
