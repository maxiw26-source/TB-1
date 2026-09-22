"""ADA V8 paper-forward monitor: balanced + expiry2 + LONG FVG >= 0.15 ATR.

Reuses the proven BTC V8 paper simulation in an *independent process*.
Changes BTC module globals only in this ADA process; no real order API is
imported or called. ADA state/events have separate filenames.
"""
import json
import sys
import time
from pathlib import Path

import live_paper_v8_btc as paper

PAPER_SYMBOL = "ADAUSDT"
MIN_LONG_FVG_ATR = 0.15

# BTC monitor runs as a separate systemd process; this overrides only
# this ADA process's copy of the Python module.
paper.SYMBOL = PAPER_SYMBOL
paper.PROFILE = dict(paper.PROFILE)
paper.PROFILE["name"] = "ada_balanced_expiry2_long_fvg015"
paper.PROFILE["setup_expiry_candles"] = 2
paper.STATE_FILE = Path("v8_ada_paper_state.json")
paper.EVENT_FILE = Path("v8_ada_paper_events.jsonl")

_original_calculate_signal = paper.calculate_signal
_original_notify = paper.notify


def passes_filter(result):
    """Additional filter only for LONG; normal balanced rules also apply."""
    if result.get("signal") != "PENDING_LONG":
        return True
    indicators = result.get("indicators") or {}
    setup = indicators.get("setup") or {}
    atr = float(indicators.get("atr") or 0.0)
    if atr <= 0 or "fvg_upper" not in setup or "fvg_lower" not in setup:
        return False
    fvg_atr = (
        float(setup["fvg_upper"]) - float(setup["fvg_lower"])
    ) / atr
    return fvg_atr >= MIN_LONG_FVG_ATR


def filtered_signal(*args, **kwargs):
    result = _original_calculate_signal(*args, **kwargs)
    if not passes_filter(result):
        return {
            "signal": None,
            "reason": "ADA LONG FVG/ATR unter 0.15",
            "indicators": result.get("indicators") or {},
        }
    return result


def notify_ada(message):
    _original_notify(message.replace("V8 BTC PAPER", "V8 ADA PAPER"))


paper.calculate_signal = filtered_signal
paper.notify = notify_ada


def run():
    state = paper.load_state()
    print("LSOB V8 ADA PAPER-FORWARD läuft.", flush=True)
    print("Balanced + expiry2 + LONG FVG >= 0.15 ATR.", flush=True)
    print("Separater Paper-Prozess, KEINE echten Orders.", flush=True)

    while True:
        try:
            one_minute = paper.closed_candles("1m", limit=5)
            if not one_minute:
                time.sleep(paper.POLL_SECONDS)
                continue

            candle = one_minute[-1]
            candle_ts = int(candle["timestamp"])
            if state.get("last_closed_candle") != candle_ts:
                state["last_closed_candle"] = candle_ts
                paper.process_candle(state, candle)
                paper.analyze_for_setup(state)
                paper.save_state(state)
            time.sleep(paper.POLL_SECONDS)
        except KeyboardInterrupt:
            paper.save_state(state)
            return
        except Exception as exc:
            paper.log_event("LOOP_ERROR", {"error": str(exc)})
            print("ADA PAPER Fehler:", exc, flush=True)
            time.sleep(paper.POLL_SECONDS)


def main():
    command = sys.argv[1].lower() if len(sys.argv) > 1 else "run"
    if command == "run":
        run()
    elif command == "status":
        state = paper.load_state()
        print(json.dumps(state, indent=2, ensure_ascii=False))
        print()
        print(
            paper.status_text(state).replace(
                "V8 BTC PAPER", "V8 ADA PAPER"
            )
        )
    else:
        print("Verfügbar: run, status")


if __name__ == "__main__":
    main()
