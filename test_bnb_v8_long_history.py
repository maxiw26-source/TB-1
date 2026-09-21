import subprocess
import sys


def run(command):
    print("")
    print("$", " ".join(command))
    subprocess.run(
        command,
        check=True,
    )


def main():
    print("BNB V8 LANGZEITTEST")
    print(
        "Schritt 1: bis zu 1000 Tage "
        "BNBUSDT-Daten laden/ergänzen."
    )

    run(
        [
            sys.executable,
            "historical_data.py",
            "--days",
            "1000",
            "--symbols",
            "BNBUSDT",
        ]
    )

    print("")
    print(
        "Schritt 2: bis zu 10 getrennte "
        "90-Tage-Blöcke testen."
    )

    run(
        [
            sys.executable,
            "backtest_bnb_v8_long_history.py",
        ]
    )

    print("")
    print("BNB V8 LANGZEITTEST FERTIG")


if __name__ == "__main__":
    main()
