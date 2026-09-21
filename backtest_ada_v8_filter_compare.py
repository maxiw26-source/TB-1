from copy import deepcopy
from datetime import datetime, timezone

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

CANDIDATES = []

base = deepcopy(BALANCED)
base["name"] = "balanced_base"
CANDIDATES.append(base)

adx20 = deepcopy(BALANCED)
adx20["name"] = "adx20"
adx20["adx_minimum"] = 20.0
CANDIDATES.append(adx20)

adx22 = deepcopy(BALANCED)
adx22["name"] = "adx22"
adx22["adx_minimum"] = 22.0
CANDIDATES.append(adx22)

fvg10 = deepcopy(BALANCED)
fvg10["name"] = "fvg10"
fvg10["min_fvg_atr_ratio"] = 0.10
CANDIDATES.append(fvg10)

strict_combo = deepcopy(BALANCED)
strict_combo["name"] = "strict_combo"
strict_combo["adx_minimum"] = 22.0
strict_combo["min_fvg_atr_ratio"] = 0.10
strict_combo["sweep_buffer_atr"] = 0.05
CANDIDATES.append(strict_combo)

shorter_expiry = deepcopy(BALANCED)
shorter_expiry["name"] = "expiry4"
shorter_expiry["setup_expiry_candles"] = 4
CANDIDATES.append(shorter_expiry)


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


def run_candidate(profile, one_minute):
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

    positive_blocks = sum(
        1
        for item in results
        if (
            item["trades"] > 0
            and item["net"] > 0
            and item["pf"] > 1.0
        )
    )

    return {
        "name": profile["name"],
        "trades": sum(
            item["trades"]
            for item in results
        ),
        "positive_blocks": positive_blocks,
        "active_blocks": sum(
            1
            for item in results
            if item["trades"] > 0
        ),
        "net": sum(
            item["net"]
            for item in results
        ),
        "pf": aggregate_pf(results),
        "blocks": results,
    }


def main():
    one_minute = load_csv(
        SYMBOL,
        "1m",
    )

    print(
        "ADA V8 – FILTER-STUDIE"
    )
    print(
        "Ziel: weniger schwache Trades, "
        "ohne Parameterwildwuchs."
    )
    print(
        "10 x 90-Tage-Blöcke | "
        "keine Orders | keine Live-Änderung"
    )
    print("")
    print(
        "Getestet werden nur kleine Änderungen "
        "am vorhandenen balanced-Profil."
    )

    summaries = []

    for profile in CANDIDATES:
        result = run_candidate(
            profile,
            one_minute,
        )
        summaries.append(result)

        print("")
        print(
            profile["name"],
            "| Trades:",
            result["trades"],
            "| Positive Blöcke:",
            f'{result["positive_blocks"]}/{result["active_blocks"]}',
            "| PF:",
            pf_text(result["pf"]),
            "| Netto:",
            f'{result["net"]:.2f} USDT',
        )

    print("")
    print("================================")
    print("DETAILS PRO BLOCK")
    print("================================")

    for summary in summaries:
        print("")
        print(summary["name"])

        for index, block in enumerate(
            summary["blocks"],
            start=1,
        ):
            print(
                f"Block {index}:",
                "Trades",
                block["trades"],
                "| PF",
                pf_text(block["pf"]),
                "| Netto",
                f'{block["net"]:.2f}',
            )

    print("")
    print(
        "Hinweis: Diese Varianten wurden auf "
        "bereits bekannten historischen Daten getestet. "
        "Ein besseres Ergebnis hier ist noch kein "
        "unabhängiger Beweis für bessere Zukunftsleistung."
    )


if __name__ == "__main__":
    main()
