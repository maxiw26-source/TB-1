from datetime import datetime, timezone

from backtest_v8_walkforward import (
    PROFILES,
    load_csv,
    run_window,
)

SYMBOL = "BNBUSDT"
BLOCK_DAYS = 90
BLOCK_COUNT = 10

BALANCED = next(
    profile
    for profile in PROFILES
    if profile["name"] == "balanced"
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

    available_ms = (
        end_ts
        - one_minute[0]["timestamp"]
    )

    max_blocks = max(
        1,
        int(available_ms // block_ms),
    )

    block_count = min(
        BLOCK_COUNT,
        max_blocks,
    )

    results = []

    print(
        "BNB V8 BALANCED – LANGZEIT ROBUSTHEITSTEST"
    )
    print(
        block_count,
        "getrennte Blöcke à",
        BLOCK_DAYS,
        "Tage",
    )
    print(
        "Festes Profil: balanced | "
        "LONG + SHORT | keine Orders"
    )

    for block_number in range(
        block_count,
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
            BALANCED,
            block_start,
            block_end,
        )

        results.append(
            result
        )

        print("")
        print(
            f"BLOCK {block_count - block_number + 1}"
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

    active_blocks = sum(
        1
        for result in results
        if result["trades"] > 0
    )

    profitable_active_blocks = sum(
        1
        for result in results
        if (
            result["trades"] > 0
            and result["net"] > 0
            and result["pf"] > 1.0
        )
    )

    print("")
    print("GESAMT")
    print(
        "Positive Blöcke:",
        positive_blocks,
        "/",
        block_count,
    )
    print(
        "Aktive Blöcke:",
        active_blocks,
        "/",
        block_count,
    )
    print(
        "Profitable aktive Blöcke:",
        profitable_active_blocks,
        "/",
        active_blocks if active_blocks else 0,
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
        "Hinweis: Das ist ein Langzeit-Robustheitstest "
        "des bereits gewählten balanced-Profils, "
        "kein neuer unabhängiger Out-of-Sample-Test."
    )


if __name__ == "__main__":
    main()
