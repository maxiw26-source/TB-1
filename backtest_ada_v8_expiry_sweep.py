from copy import deepcopy

from backtest_v8_walkforward import (
    PROFILES,
    load_csv,
    run_window,
)

SYMBOL = "ADAUSDT"
BLOCK_DAYS = 90
BLOCK_COUNT = 10

BALANCED = next(
    deepcopy(profile)
    for profile in PROFILES
    if profile["name"] == "balanced"
)

EXPIRIES = [2, 3, 4, 5, 6]


def pf_text(value):
    return (
        "inf"
        if value == float("inf")
        else f"{value:.2f}"
    )


def aggregate_pf(results):
    profit = sum(
        max(item["net"], 0.0)
        for item in results
    )
    loss = abs(
        sum(
            min(item["net"], 0.0)
            for item in results
        )
    )

    if loss > 0:
        return profit / loss
    if profit > 0:
        return float("inf")
    return 0.0


def run_candidate(expiry, one_minute):
    profile = deepcopy(BALANCED)
    profile["name"] = f"expiry{expiry}"
    profile["setup_expiry_candles"] = expiry

    end_ts = one_minute[-1]["timestamp"]
    block_ms = BLOCK_DAYS * 24 * 60 * 60 * 1000
    available_ms = end_ts - one_minute[0]["timestamp"]
    max_blocks = max(1, int(available_ms // block_ms))
    block_count = min(BLOCK_COUNT, max_blocks)

    results = []

    for block_number in range(block_count, 0, -1):
        block_end = end_ts - (block_number - 1) * block_ms
        block_start = block_end - block_ms + 60_000

        result = run_window(
            SYMBOL,
            profile,
            block_start,
            block_end,
        )
        results.append(result)

    return {
        "expiry": expiry,
        "name": profile["name"],
        "trades": sum(x["trades"] for x in results),
        "positive_blocks": sum(
            1
            for x in results
            if x["trades"] > 0
            and x["net"] > 0
            and x["pf"] > 1.0
        ),
        "active_blocks": sum(
            1 for x in results if x["trades"] > 0
        ),
        "net": sum(x["net"] for x in results),
        "pf": aggregate_pf(results),
        "blocks": results,
    }


def main():
    one_minute = load_csv(SYMBOL, "1m")

    print("ADA V8 – EXPIRY-SWEEP")
    print("Balanced-Profil, nur Setup-Expiry verändert")
    print("Expiry 2 / 3 / 4 / 5 / 6")
    print("10 x 90-Tage-Blöcke | keine Orders")

    summaries = []

    for expiry in EXPIRIES:
        summary = run_candidate(expiry, one_minute)
        summaries.append(summary)

        print("")
        print(
            summary["name"],
            "| Trades:",
            summary["trades"],
            "| Positive Blöcke:",
            f'{summary["positive_blocks"]}/{summary["active_blocks"]}',
            "| PF:",
            pf_text(summary["pf"]),
            "| Netto:",
            f'{summary["net"]:.2f} USDT',
        )

    print("")
    print("================================")
    print("BLOCK-DETAILS")
    print("================================")

    for summary in summaries:
        print("")
        print(summary["name"])
        for i, block in enumerate(summary["blocks"], start=1):
            print(
                f"Block {i}:",
                "Trades",
                block["trades"],
                "| L/S",
                f'{block["longs"]}/{block["shorts"]}',
                "| PF",
                pf_text(block["pf"]),
                "| Netto",
                f'{block["net"]:.2f}',
            )

    print("")
    print("================================")
    print("DIREKTVERGLEICH")
    print("================================")

    for summary in summaries:
        print(
            summary["name"],
            "| Blöcke:",
            f'{summary["positive_blocks"]}/{summary["active_blocks"]}',
            "| Trades:",
            summary["trades"],
            "| PF:",
            pf_text(summary["pf"]),
            "| Netto:",
            f'{summary["net"]:.2f} USDT',
        )

    print("")
    print(
        "Hinweis: Das ist ein Parameter-Sensitivitätstest "
        "auf bereits bekannten historischen Daten. "
        "Ein einzelner bester Wert kann Überanpassung sein."
    )


if __name__ == "__main__":
    main()
