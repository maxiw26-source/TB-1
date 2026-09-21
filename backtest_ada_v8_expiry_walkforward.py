from copy import deepcopy

from backtest_v8_walkforward import (
    PROFILES,
    load_csv,
    run_window,
)

SYMBOL = "ADAUSDT"
BLOCK_DAYS = 90
BLOCK_COUNT = 10
EXPIRIES = [2, 3, 4, 5, 6]
MIN_TRAIN_BLOCKS = 3

BALANCED = next(
    deepcopy(profile)
    for profile in PROFILES
    if profile["name"] == "balanced"
)


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


def score_candidate(block_results):
    positive = sum(
        1
        for item in block_results
        if (
            item["trades"] > 0
            and item["net"] > 0
            and item["pf"] > 1.0
        )
    )

    net = sum(
        item["net"]
        for item in block_results
    )

    pf = aggregate_pf(block_results)

    trades = sum(
        item["trades"]
        for item in block_results
    )

    return (
        positive,
        pf,
        net,
        trades,
    )


def build_blocks(one_minute):
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

    blocks = []

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

        blocks.append(
            (block_start, block_end)
        )

    return blocks


def make_profile(expiry):
    profile = deepcopy(BALANCED)
    profile["name"] = f"expiry{expiry}"
    profile["setup_expiry_candles"] = expiry
    return profile


def run_block(expiry, block):
    block_start, block_end = block

    return run_window(
        SYMBOL,
        make_profile(expiry),
        block_start,
        block_end,
    )


def main():
    one_minute = load_csv(
        SYMBOL,
        "1m",
    )

    blocks = build_blocks(
        one_minute
    )

    print(
        "ADA V8 – EXPIRY WALK-FORWARD"
    )
    print(
        "Nur vergangene Blöcke wählen den "
        "Expiry-Wert für den nächsten Block."
    )
    print(
        f"Expiry-Kandidaten: {EXPIRIES}"
    )
    print(
        f"Mindestens {MIN_TRAIN_BLOCKS} "
        "Trainingsblöcke vor dem ersten Test."
    )
    print(
        "Keine Orders / keine Live-Änderung"
    )

    cache = {
        expiry: [
            run_block(
                expiry,
                block,
            )
            for block in blocks
        ]
        for expiry in EXPIRIES
    }

    oos_results = []

    for test_index in range(
        MIN_TRAIN_BLOCKS,
        len(blocks),
    ):
        candidate_scores = []

        for expiry in EXPIRIES:
            train_results = cache[expiry][
                :test_index
            ]

            candidate_scores.append(
                (
                    score_candidate(
                        train_results
                    ),
                    expiry,
                )
            )

        candidate_scores.sort(
            reverse=True
        )

        best_score, chosen_expiry = (
            candidate_scores[0]
        )

        test_result = cache[
            chosen_expiry
        ][test_index]

        oos_results.append(
            {
                "block": test_index + 1,
                "expiry": chosen_expiry,
                "train_positive": (
                    best_score[0]
                ),
                "train_pf": (
                    best_score[1]
                ),
                "train_net": (
                    best_score[2]
                ),
                "test": test_result,
            }
        )

        print("")
        print(
            f"TEST-BLOCK {test_index + 1}"
        )
        print(
            "Gewählter Expiry:",
            chosen_expiry,
        )
        print(
            "Training bis Block",
            test_index,
            "| Positive:",
            best_score[0],
            "| PF:",
            pf_text(best_score[1]),
            "| Netto:",
            f"{best_score[2]:.2f}",
        )
        print(
            "OOS:",
            "Trades",
            test_result["trades"],
            "| L/S",
            f'{test_result["longs"]}/'
            f'{test_result["shorts"]}',
            "| Trefferquote",
            f'{test_result["win_rate"]:.1f}%',
            "| PF",
            pf_text(
                test_result["pf"]
            ),
            "| Netto",
            f'{test_result["net"]:.2f}',
        )

    tests = [
        item["test"]
        for item in oos_results
    ]

    positive_oos = sum(
        1
        for item in tests
        if (
            item["trades"] > 0
            and item["net"] > 0
            and item["pf"] > 1.0
        )
    )

    total_trades = sum(
        item["trades"]
        for item in tests
    )

    total_net = sum(
        item["net"]
        for item in tests
    )

    total_pf = aggregate_pf(
        tests
    )

    print("")
    print(
        "================================"
    )
    print(
        "WALK-FORWARD GESAMT"
    )
    print(
        "================================"
    )
    print(
        "OOS-Blöcke:",
        len(tests),
    )
    print(
        "Positive OOS-Blöcke:",
        positive_oos,
        "/",
        len(tests),
    )
    print(
        "OOS-Trades:",
        total_trades,
    )
    print(
        "OOS PF:",
        pf_text(total_pf),
    )
    print(
        "OOS Netto:",
        f"{total_net:.2f} USDT",
    )

    print("")
    print(
        "Gewählte Expiries:",
        ", ".join(
            str(item["expiry"])
            for item in oos_results
        ),
    )

    print("")
    print(
        "Hinweis: Dieser Test reduziert "
        "Look-ahead bei der Parameterauswahl, "
        "ist aber weiterhin eine historische "
        "Simulation und keine Garantie für "
        "zukünftige Ergebnisse."
    )


if __name__ == "__main__":
    main()
