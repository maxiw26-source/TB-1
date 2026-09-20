import backtest_v7 as v7

v7.SYMBOLS = ["SOLUSDT"]
v7.BACKTEST_DAYS = 365


def main():
    print("SOLUSDT – V7 PAPER-BACKTEST")
    print("365 Tage, gleiche V7-Logik, keine Live-Änderung.")
    result = v7.run_backtest("SOLUSDT")
    print("")
    print("SOL V7 Ergebnis:")
    print(
        "Trades:", result["trades"],
        "| Netto:", round(result["net_pnl"], 2), "USDT",
        "| PF:", round(result["profit_factor"], 2),
    )


if __name__ == "__main__":
    main()
