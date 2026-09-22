from bisect import bisect_right
from copy import deepcopy

from backtest_v8_walkforward import (
    CANDLE_LIMIT,
    PROFILES,
    START_BALANCE,
    check_position,
    close_part,
    create_position,
    finalize,
    history_before,
    load_csv,
    stats,
)
from strategy_v8 import calculate_signal

SYMBOL = "ADAUSDT"
BLOCK_DAYS = 90
BLOCK_COUNT = 10

BASE = next(
    deepcopy(profile)
    for profile in PROFILES
    if profile["name"] == "balanced"
)
BASE["setup_expiry_candles"] = 2

VARIANTS = [
    {
        "name": "expiry2_base",
        "short_only": False,
        "long_min_fvg_atr": 0.0,
        "long_min_risk_atr": 0.0,
    },
    {
        "name": "short_only",
        "short_only": True,
        "long_min_fvg_atr": 0.0,
        "long_min_risk_atr": 0.0,
    },
    {
        "name": "long_fvg_015",
        "short_only": False,
        "long_min_fvg_atr": 0.15,
        "long_min_risk_atr": 0.0,
    },
    {
        "name": "long_fvg_025",
        "short_only": False,
        "long_min_fvg_atr": 0.25,
        "long_min_risk_atr": 0.0,
    },
    {
        "name": "long_risk_300",
        "short_only": False,
        "long_min_fvg_atr": 0.0,
        "long_min_risk_atr": 3.0,
    },
    {
        "name": "long_fvg015_risk300",
        "short_only": False,
        "long_min_fvg_atr": 0.15,
        "long_min_risk_atr": 3.0,
    },
]


def pf_text(value):
    return "inf" if value == float("inf") else f"{value:.2f}"


def aggregate_pf(results):
    profit = sum(max(x["net"], 0.0) for x in results)
    loss = abs(sum(min(x["net"], 0.0) for x in results))
    if loss > 0:
        return profit / loss
    if profit > 0:
        return float("inf")
    return 0.0


def passes_variant(setup, indicators, variant):
    side = setup["side"]

    if variant["short_only"] and side == "LONG":
        return False

    if side != "LONG":
        return True

    atr_value = float(indicators.get("atr") or 0.0)
    if atr_value <= 0:
        return False

    fvg_size = abs(
        float(setup["fvg_upper"])
        - float(setup["fvg_lower"])
    )
    fvg_ratio = fvg_size / atr_value

    risk_distance = abs(
        float(setup["entry"])
        - float(setup["stop"])
    )
    risk_ratio = risk_distance / atr_value

    if fvg_ratio < float(variant["long_min_fvg_atr"]):
        return False

    if risk_ratio < float(variant["long_min_risk_atr"]):
        return False

    return True


def run_window_filtered(start_ts, end_ts, variant):
    entry_all = load_csv(SYMBOL, "1m")
    confirmation_all = load_csv(SYMBOL, "5m")
    trend_all = load_csv(SYMBOL, "15m")

    warmup = 4 * 24 * 60 * 60 * 1000

    entry = [
        c for c in entry_all
        if start_ts - warmup <= c["timestamp"] <= end_ts
    ]
    confirmation = [
        c for c in confirmation_all
        if start_ts - warmup <= c["timestamp"] <= end_ts
    ]
    trend = [
        c for c in trend_all
        if start_ts - warmup <= c["timestamp"] <= end_ts
    ]

    confirmation_times = [c["timestamp"] for c in confirmation]
    trend_times = [c["timestamp"] for c in trend]

    balance = START_BALANCE
    trades = []
    pending = None
    position = None
    used_setups = set()
    cooldown_until = -1

    for index, candle in enumerate(entry):
        timestamp = candle["timestamp"]

        if timestamp < start_ts:
            continue

        if position is not None:
            if check_position(position, candle):
                trade = finalize(position, timestamp)
                trades.append(trade)
                balance += trade["net_pnl"]
                position = None
                cooldown_until = index + 10
            continue

        if pending is not None:
            setup = pending["setup"]
            age = index - pending["created_index"]

            invalid = (
                float(candle["low"]) <= float(setup["stop"])
                if setup["side"] == "LONG"
                else float(candle["high"]) >= float(setup["stop"])
            )

            if invalid or age > int(BASE["setup_expiry_candles"]):
                pending = None
            else:
                entry_price = float(setup["entry"])
                if (
                    float(candle["low"])
                    <= entry_price
                    <= float(candle["high"])
                ):
                    position = create_position(
                        setup,
                        balance,
                        BASE,
                        timestamp,
                    )
                    pending = None

                    if position is not None:
                        continue

        if pending is not None or index <= cooldown_until:
            continue

        entry_history = entry[
            max(0, index - CANDLE_LIMIT + 1):
            index + 1
        ]

        confirmation_history = history_before(
            confirmation,
            confirmation_times,
            timestamp,
        )
        trend_history = history_before(
            trend,
            trend_times,
            timestamp,
        )

        result = calculate_signal(
            entry_history,
            confirmation_history,
            trend_history,
            BASE,
        )

        if result.get("signal") not in {
            "PENDING_LONG",
            "PENDING_SHORT",
        }:
            continue

        indicators = result.get("indicators") or {}
        setup_id = indicators.get("setup_id")
        setup = indicators.get("setup")

        if not setup_id or not setup or setup_id in used_setups:
            continue

        used_setups.add(setup_id)

        if not passes_variant(
            setup,
            indicators,
            variant,
        ):
            continue

        pending = {
            "setup": setup,
            "created_index": index,
        }

    if position is not None and entry:
        last = entry[-1]
        close_part(
            position,
            float(last["close"]),
            position["remaining"],
            "WINDOW_END",
        )
        trades.append(
            finalize(
                position,
                last["timestamp"],
            )
        )

    return stats(trades)


def blocks(one_minute):
    end_ts = one_minute[-1]["timestamp"]
    block_ms = BLOCK_DAYS * 24 * 60 * 60 * 1000
    available_ms = end_ts - one_minute[0]["timestamp"]
    count = min(
        BLOCK_COUNT,
        max(1, int(available_ms // block_ms)),
    )

    result = []
    for block_number in range(count, 0, -1):
        block_end = end_ts - (block_number - 1) * block_ms
        block_start = block_end - block_ms + 60_000
        result.append((block_start, block_end))
    return result


def main():
    one_minute = load_csv(SYMBOL, "1m")
    windows = blocks(one_minute)

    print("ADA V8 EXPIRY2 – GEZIELTER FILTER-TEST")
    print("Ausgangspunkt: balanced + expiry2")
    print("Ziel: LONG-Verlustphasen filtern, ohne blind zu optimieren.")
    print("Keine Orders / keine Live-Änderung")

    summaries = []

    for variant in VARIANTS:
        results = [
            run_window_filtered(start, end, variant)
            for start, end in windows
        ]

        summary = {
            "name": variant["name"],
            "trades": sum(x["trades"] for x in results),
            "positive": sum(
                1
                for x in results
                if x["trades"] > 0
                and x["net"] > 0
                and x["pf"] > 1.0
            ),
            "active": sum(1 for x in results if x["trades"] > 0),
            "net": sum(x["net"] for x in results),
            "pf": aggregate_pf(results),
            "blocks": results,
        }
        summaries.append(summary)

        print("")
        print(
            summary["name"],
            "| Blöcke:",
            f'{summary["positive"]}/{summary["active"]}',
            "| Trades:",
            summary["trades"],
            "| PF:",
            pf_text(summary["pf"]),
            "| Netto:",
            f'{summary["net"]:.2f} USDT',
        )

    print("")
    print("================================")
    print("BLOCK 5 + 6")
    print("================================")
    for summary in summaries:
        b5 = summary["blocks"][4]
        b6 = summary["blocks"][5]
        print(
            summary["name"],
            "| B5:",
            f'{b5["net"]:.2f}',
            "PF",
            pf_text(b5["pf"]),
            "| B6:",
            f'{b6["net"]:.2f}',
            "PF",
            pf_text(b6["pf"]),
        )

    print("")
    print(
        "Hinweis: Diese Varianten wurden aus bekannten "
        "Verlustblöcken abgeleitet. Ein besseres Ergebnis "
        "muss danach wieder Walk-Forward/OOS validiert werden."
    )


if __name__ == "__main__":
    main()
