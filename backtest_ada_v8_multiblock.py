from datetime import datetime, timezone

from backtest_v8_walkforward import (
    PROFILES,
    load_csv,
    run_window,
)

SYMBOL = "ADAUSDT"
BLOCK_DAYS = 90
BLOCK_COUNT = 4

FREQUENT = next(
    profile
    for profile in PROFILES
    if profile["name"] == "frequent"
)


def date_text(timestamp_ms):
    return datetime.fromtimestamp(
        timestamp_ms / 1000,
        tz=timezone.utc,
    ).date().isoformat()


def pf_text(value):
    return (
        "inf"
        if value == float("inf")
        else f"{value:.2f}"
    )


def main():
    one_minute = load_csv(
        SYMBOL,
        "1m",
    )

    end_ts = one_minute[-1]["timestamp"]
    block_ms = (
        BLOCK_DAYS
        * 24
        * 60
        * 60
        * 1000
    )

    results = []

    print(
        "ADA V8 FREQUENT – 4-BLOCK ROBUSTHEITSTEST"
    )
    print(
        "4 getrennte Blöcke à",
        BLOCK_DAYS,
        "Tage",
    )
    print(
        "Festes Profil: frequent | "
        "LONG + SHORT | keine Orders"
    )

    for block_number in range(
        BLOCK_COUNT,
        0,
        -1,
    ):
        block_end = (
            end_ts
            - (block_number - 1)
            * block_ms
        )

        block_start = (
            block_end
            - block_ms
            + 60_000
        )

        result = run_window(
            SYMBOL,
            FREQUENT,
            block_start,
            block_end,
        )

        results.append(
            result
        )

        print("")
        print(
            f"BLOCK {BLOCK_COUNT - block_number + 1}"
        )
        print(
            date_text(block_start),
            "bis",
            date_text(block_end),
        )
        print(
            "Trades:",
            result["trades"],
            "| L/S:",
            f'{result["longs"]}/{result["shorts"]}',
            "| Trefferquote:",
            f'{result["win_rate"]:.1f}%',
            "| PF:",
            pf_text(result["pf"]),
            "| Netto:",
            f'{result["net"]:.2f} USDT',
        )

    total_trades = sum(
        result["trades"]
        for result in results
    )

    total_net = sum(
        result["net"]
        for result in results
    )

    positive_blocks = sum(
        1
        for result in results
        if (
            result["net"] > 0
            and result["pf"] > 1.0
        )
    )

    print("")
    print("GESAMT")
    print(
        "Positive Blöcke:",
        positive_blocks,
        "/",
        BLOCK_COUNT,
    )
    print(
        "Trades:",
        total_trades,
    )
    print(
        "Netto über Blöcke:",
        f"{total_net:.2f} USDT",
    )

    print("")
    print(
        "Hinweis: Das ist ein Robustheitstest "
        "des bereits im Walk-Forward gewählten "
        "frequent-Profils, kein neuer unabhängiger "
        "Out-of-Sample-Test."
    )


if __name__ == "__main__":
    main()
