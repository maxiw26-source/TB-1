import subprocess
import sys


SYMBOLS = [
    "XRPUSDT",
    "BNBUSDT",
]


def run(command):
    print("")
    print("$", " ".join(command))
    subprocess.run(
        command,
        check=True,
    )


def main():
    print("LSOB V8 – XRP + BNB KANDIDATENTEST")
    print(
        "Schritt 1: 365 Tage historische Daten "
        "für XRPUSDT und BNBUSDT laden."
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
    print("XRP + BNB KANDIDATENTEST FERTIG")


if __name__ == "__main__":
    main()
