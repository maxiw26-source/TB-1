import subprocess
import sys


SYMBOLS = [
    "ADAUSDT",
    "LINKUSDT",
]


def run(command):
    print("")
    print("$", " ".join(command))
    subprocess.run(
        command,
        check=True,
    )


def main():
    print("LSOB V8 – ADA + LINK KANDIDATENTEST")
    print(
        "Schritt 1: 365 Tage historische Daten "
        "für ADAUSDT und LINKUSDT laden."
    )

    run(
        [
            sys.executable,
            "historical_data.py",
            "--days",
            "365",
            "--symbols",
            *SYMBOLS,
        ]
    )

    print("")
    print(
        "Schritt 2: V8 Walk-Forward "
        "245 Tage Training + 120 Tage OOS."
    )

    run(
        [
            sys.executable,
            "backtest_v8_walkforward.py",
            "--symbols",
            *SYMBOLS,
        ]
    )

    print("")
    print("ADA + LINK KANDIDATENTEST FERTIG")


if __name__ == "__main__":
    main()
