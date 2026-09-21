import os

import live_auto_mode as auto


def main():
    original = dict(os.environ)

    try:
        print("1/4 Standard ist AUS")
        os.environ.pop(
            "ENABLE_AUTO_LIVE_EXECUTION",
            None,
        )
        os.environ.pop(
            "AUTO_LIVE_EXECUTION_ACK",
            None,
        )
        assert auto.auto_live_active() is False

        print("2/4 Schalter allein reicht nicht")
        os.environ[
            "ENABLE_AUTO_LIVE_EXECUTION"
        ] = "true"
        assert auto.auto_live_active() is False

        print("3/4 Falscher ACK bleibt AUS")
        os.environ[
            "AUTO_LIVE_EXECUTION_ACK"
        ] = "FALSCH"
        assert auto.auto_live_active() is False

        print("4/4 Exakter ACK aktiviert")
        os.environ[
            "AUTO_LIVE_EXECUTION_ACK"
        ] = auto.AUTO_ACK
        assert auto.auto_live_active() is True

        print("")
        print(
            "AUTO-LIVE SELFTEST ERFOLGREICH"
        )
        print(
            "Keine echte Order wurde gesendet."
        )

    finally:
        os.environ.clear()
        os.environ.update(original)


if __name__ == "__main__":
    main()
