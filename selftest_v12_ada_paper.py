"""Offline V12 forward-paper checks without network or orders."""
import tempfile
from pathlib import Path

import live_paper_v12_ada as bot


def candle(ts, price=100, high=101, low=99):
    return {"timestamp": ts, "open": price, "high": high, "low": low,
            "close": price, "volume": 10}


def main():
    h = bot.HOUR_MS
    with tempfile.TemporaryDirectory() as folder:
        saved = (bot.STATE, bot.EVENTS, bot.fetch_closed, bot.fetch_price,
                 bot.signals, bot.notify)
        try:
            bot.STATE = Path(folder) / "paper.json"
            bot.EVENTS = Path(folder) / "events.jsonl"
            bot.fetch_closed = lambda now: [candle(i * h) for i in range(
                now // h - 300, now // h)]
            bot.fetch_price = lambda: 100.0
            bot.signals = lambda bars: [None] * (len(bars) - 1) + [("LONG", 1.0)]
            bot.notify = lambda message: None
            state = bot.load_state()
            assert bot.process(state, 301 * h + 1000) == "START"
            assert state["position"] is None and state["stats"]["trades"] == 0
            assert bot.process(state, 301 * h + 2000) == "WAIT"
            assert bot.process(state, 302 * h + 1000) == "PROCESSED"
            assert state["position"] is not None
            assert abs(state["position"]["entry"] * state["position"]["qty"] - 100) < 1e-7
            assert bot.load_state()["position"] == state["position"]
            assert bot.process(state, 302 * h + 2000) == "WAIT"
            # Next closed bar touches both stop and target: stop must win.
            def next_closed(now):
                bars = [candle(i * h) for i in range(now // h - 300, now // h)]
                bars[-1] = candle(bars[-1]["timestamp"], high=110, low=90)
                return bars
            bot.fetch_closed = next_closed
            bot.signals = lambda bars: [None] * len(bars)
            bot.process(state, 303 * h + 1000)
            assert state["position"] is None
            assert state["stats"]["trades"] == 1
            assert state["stats"]["net_usdt"] < 0
            assert state["stats"]["fees_usdt"] > 0
            assert '"STOP"' in bot.EVENTS.read_text(encoding="utf-8")
        finally:
            (bot.STATE, bot.EVENTS, bot.fetch_closed, bot.fetch_price,
             bot.signals, bot.notify) = saved
    print("V12 ADA PAPER SELFTEST ERFOLGREICH: no backfill, next hour, "
          "100 USDT, restart state, stop first, fees")


if __name__ == "__main__":
    main()
