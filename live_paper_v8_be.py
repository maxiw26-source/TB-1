"""Independent V8 BTC/ADA 2R + cost-covered break-even paper experiment.

Uses the existing entry engine in its own process; never imports order APIs.
OHLC cannot establish intrabar order: original stop has priority and a new
BE stop is armed at a qualifying candle CLOSE, effective next candle.
Funding is not modeled (same assumption as the V8 baseline).
"""
import argparse
import json
from pathlib import Path

POLICY = "full_2r_be1r_v1"
BUFFER_NOTIONAL_PERCENT = 0.01  # 0.01 USDT at 100 USDT notional.


def cost_stop(paper, position):
    """Solve net-at-stop = buffer using the engine's exact fee/slip model."""
    qty = float(position["remaining"])
    entry = float(position["entry"])
    buffer = float(position["paper_notional_usdt"]) * BUFFER_NOTIONAL_PERCENT / 100
    costs = float(position["fees"]) + float(position["slippage"]) + buffer
    rate = (paper.TAKER_FEE_PERCENT + paper.TAKER_SLIPPAGE_PERCENT) / 100
    if position["side"] == "LONG":
        return (entry + costs / qty) / (1 - rate)
    return (entry - costs / qty) / (1 + rate)


def check_be(paper, original_check, position, candle):
    # Already-active barriers always run before considering a new stop.
    event = original_check(position, candle)
    if event or position.get("be_armed"):
        return event
    entry = float(position["entry"])
    risk = abs(entry - float(position["initial_stop"]))
    is_long = position["side"] == "LONG"
    trigger = entry + risk if is_long else entry - risk
    reached = float(candle["high"]) >= trigger if is_long else float(candle["low"]) <= trigger
    candidate = cost_stop(paper, position)
    close = float(candle["close"])
    # Never place a stop ahead of the current simulated market price.
    covered = close > candidate if is_long else close < candidate
    inside_target = candidate < position["tp2"] if is_long else candidate > position["tp2"]
    if reached and covered and inside_target:
        position["stop"] = candidate
        position["be_armed"] = True
        position["be_armed_candle"] = candle["timestamp"]
        paper.log_event("PAPER_BE_ARMED", dict(position))
    return None


def configure(symbol):
    # Call once per process, matching the existing ADA adapter architecture.
    if symbol == "ADA":
        from live_paper_v8_ada import paper
    else:
        import live_paper_v8_btc as paper
    paper.EXIT_POLICY = POLICY
    paper.STATE_FILE = Path(f"v8_{symbol.lower()}_be_paper_state.json")
    paper.EVENT_FILE = Path(f"v8_{symbol.lower()}_be_paper_events.jsonl")
    original_build, original_check = paper.build_position, paper.check_position
    original_log = paper.log_event

    def build(setup):
        position = original_build(setup)
        position.update(be_armed=False, funding_assumed_usdt=0.0)
        return position

    def log(event, details=None):
        details = dict(details or {})
        if event == "PAPER_TRADE_CLOSED":
            if details.get("reason") == "STOP" and details.get("stop") != details.get("initial_stop"):
                details["reason"] = "BE_STOP"
            details["funding_assumed_usdt"] = 0.0
        original_log(event, details)

    paper.build_position = build
    paper.check_position = lambda p, c: check_be(paper, original_check, p, c)
    paper.log_event = log
    # Baseline notifications describe a fixed stop; experiments use dashboard.
    paper.notify = lambda message: None
    return paper


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("symbol", choices=("BTC", "ADA"))
    parser.add_argument("command", nargs="?", default="run", choices=("run", "status"))
    args = parser.parse_args()
    paper = configure(args.symbol)
    if args.command == "status":
        print(json.dumps(paper.load_state(), indent=2))
        print(f"V8 {args.symbol} PAPER BE: 2R; BE ab 1R am Kerzenschluss; Funding-Annahme 0.")
    else:
        print(f"V8 {args.symbol} separater BE-Paper-Test; Funding-Annahme 0.", flush=True)
        paper.run()


if __name__ == "__main__":
    main()
