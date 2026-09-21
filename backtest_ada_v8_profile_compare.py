from datetime import datetime, timezone

from backtest_v8_walkforward import (
    PROFILES,
    load_csv,
    run_window,
)

SYMBOL = "ADAUSDT"
BLOCK_DAYS = 90
BLOCK_COUNT = 10


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


def run_profile(profile, one_minute):
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

    print("")
    print("================================")
    print("PROFIL:", profile["name"])
    print("================================")

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
            profile,
            block_start,
            block_end,
        )

        results.append(result)

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
        item["trades"]
        for item in results
    )

    total_net = sum(
        item["net"]
        for item in results
    )

    positive_blocks = sum(
        1
        for item in results
        if (
            item["trades"] > 0
            and item["net"] > 0
            and item["pf"] > 1.0
        )
    )

    active_blocks = sum(
        1
        for item in results
        if item["trades"] > 0
    )

    weighted_profit = sum(
        max(item["net"], 0.0)
        for item in results
    )

    weighted_loss = abs(
        sum(
            min(item["net"], 0.0)
            for item in results
        )
    )

    aggregate_pf = (
        weighted_profit / weighted_loss
        if weighted_loss > 0
        else (
            float("inf")
            if weighted_profit > 0
            else 0.0
        )
    )

    summary = {
        "profile": profile["name"],
        "positive_blocks": positive_blocks,
        "active_blocks": active_blocks,
        "trades": total_trades,
        "net": total_net,
        "aggregate_pf": aggregate_pf,
    }

    print("")
    print("PROFIL-GESAMT")
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
        "Trades:",
        total_trades,
    )
    print(
        "Aggregierter PF:",
        pf_text(aggregate_pf),
    )
    print(
        "Netto:",
        f"{total_net:.2f} USDT",
    )

    return summary


def main():
    one_minute = load_csv(
        SYMBOL,
        "1m",
    )

    print(
        "ADA V8 – 1000-TAGE PROFILVERGLEICH"
    )
    print(
        "baseline vs balanced vs frequent"
    )
    print(
        "Bis zu 10 getrennte 90-Tage-Blöcke"
    )
    print(
        "Keine Orders / keine Live-Änderung"
    )

    summaries = []

    for profile in PROFILES:
        summaries.append(
            run_profile(
                profile,
                one_minute,
            )
        )

    print("")
    print("================================")
    print("DIREKTVERGLEICH")
    print("================================")

    for item in summaries:
        print(
            item["profile"],
            "| Blöcke:",
            f'{item["positive_blocks"]}/{item["active_blocks"]}',
            "| Trades:",
            item["trades"],
            "| PF:",
            pf_text(item["aggregate_pf"]),
            "| Netto:",
            f'{item["net"]:.2f} USDT',
        )

    print("")
    print(
        "Hinweis: Das ist ein deskriptiver "
        "Langzeitvergleich vorhandener Profile, "
        "kein neuer unabhängiger OOS-Test."
    )


if __name__ == "__main__":
    main()
