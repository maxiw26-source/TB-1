"""Offline guards for the ADA paper-forward monitor; no exchange calls."""
import live_paper_v8_ada as ada
import live_paper_v8_btc as paper


def signal(side, fvg_size, atr=1.0):
    return {
        "signal": f"PENDING_{side}",
        "indicators": {
            "atr": atr,
            "setup": {
                "side": side,
                "fvg_lower": 100.0,
                "fvg_upper": 100.0 + fvg_size,
            },
        },
    }


assert ada.PAPER_SYMBOL == "ADAUSDT"
assert paper.SYMBOL == "ADAUSDT"
assert paper.PROFILE["setup_expiry_candles"] == 2
assert paper.PROFILE["tp1_r"] == 1.0
assert paper.PROFILE["tp2_r"] == 1.3
assert paper.STATE_FILE.name == "v8_ada_paper_state.json"
assert paper.EVENT_FILE.name == "v8_ada_paper_events.jsonl"
assert ada.passes_filter(signal("LONG", 0.15)) is True
assert ada.passes_filter(signal("LONG", 0.10)) is False
assert ada.passes_filter(signal("LONG", 0.25)) is True
assert ada.passes_filter(signal("SHORT", 0.01)) is True
assert ada.passes_filter(signal("LONG", 0.20, atr=0)) is False
assert ada.passes_filter({"signal": None, "indicators": {}}) is True

print("V8 ADA PAPER SELFTEST ERFOLGREICH")
print("Nur offline Filter-, Profil- und Status-Tests. Keine echten Orders.")
