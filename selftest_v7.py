import json
import tempfile
from pathlib import Path

import bot_runtime
import live_paper_v7_hardened as bot


def assert_true(value, message):
    if not value:
        raise AssertionError(message)


def main():
    original_state = bot_runtime.STATE_FILE
    original_approval = bot_runtime.APPROVAL_FILE
    original_events = bot_runtime.EVENT_LOG_FILE

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        bot_runtime.STATE_FILE = base / "runtime_state.json"
        bot_runtime.APPROVAL_FILE = base / "pending_approval.json"
        bot_runtime.EVENT_LOG_FILE = base / "trade_events.csv"

        try:
            print("1/6 Pending-Setup testen")

            pending = {
                "signal": "PENDING_LONG",
                "setup": {
                    "entry": 100.0,
                    "stop": 95.0,
                    "fvg_timestamp": 1_000_000,
                },
            }

            candle = {
                "low": 99.0,
                "high": 101.0,
            }

            assert_true(
                bot.pending_entry_reached(
                    pending,
                    candle,
                ),
                "Entry wurde nicht erkannt",
            )

            print("2/6 Orderplan testen")

            plan = bot.build_order_plan(
                "BTCUSDT",
                pending,
            )

            assert_true(
                plan["side"] == "BUY",
                "Falsche Orderseite",
            )

            assert_true(
                plan["tp1"] == 105.0,
                "TP1 falsch",
            )

            assert_true(
                plan["tp2"] == 106.0,
                "TP2 falsch",
            )

            print("3/6 Approval-Datei testen")

            approval = bot_runtime.create_approval(
                plan
            )

            assert_true(
                approval["status"]
                == "WAITING",
                "Approval nicht WAITING",
            )

            print("4/6 Bestätigung testen")

            approved = bot_runtime.approve_order()

            assert_true(
                approved is not None
                and approved["status"]
                == "APPROVED",
                "Approval fehlgeschlagen",
            )

            print("5/6 Positionszustand testen")

            bot.PENDING_SETUPS.clear()
            bot.LAST_PROCESSED_CANDLE.clear()
            bot.OPEN_POSITIONS.clear()
            bot.LAST_SETUP_ID.clear()

            bot.open_position_from_plan(
                plan
            )

            assert_true(
                "BTCUSDT"
                in bot.OPEN_POSITIONS,
                "Position wurde nicht gespeichert",
            )

            bot.save_state()

            bot.OPEN_POSITIONS.clear()
            bot.load_state()

            assert_true(
                "BTCUSDT"
                in bot.OPEN_POSITIONS,
                "Position wurde nach Neustart nicht geladen",
            )

            print("6/6 TP1/Break-even testen")

            position = bot.OPEN_POSITIONS[
                "BTCUSDT"
            ]

            bot.update_position_state(
                position,
                "TP1",
            )

            assert_true(
                position["tp1_hit"] is True,
                "TP1 wurde nicht markiert",
            )

            assert_true(
                position["stop"]
                == position["entry"],
                "Break-even wurde nicht gesetzt",
            )

            print("")
            print("SELFTEST ERFOLGREICH")
            print(
                json.dumps(
                    {
                        "plan": plan,
                        "position": position,
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )

        finally:
            bot_runtime.STATE_FILE = original_state
            bot_runtime.APPROVAL_FILE = original_approval
            bot_runtime.EVENT_LOG_FILE = original_events


if __name__ == "__main__":
    main()
