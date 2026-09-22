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
TARGET_BLOCKS = {5, 6}

PROFILE = next(
    deepcopy(profile)
    for profile in PROFILES
    if profile["name"] == "balanced"
)
PROFILE["name"] = "balanced_expiry2"
PROFILE["setup_expiry_candles"] = 2


def pf_text(value):
    if value == float("inf"):
        return "inf"
    return f"{value:.2f}"


def run_window_detailed(start_ts, end_ts):
    entry_all = load_csv(SYMBOL, "1m")
    confirmation_all = load_csv(SYMBOL, "5m")
    trend_all = load_csv(SYMBOL, "15m")

    warmup = 4 * 24 * 60 * 60 * 1000

    entry = [
        candle for candle in entry_all
        if start_ts - warmup <= candle["timestamp"] <= end_ts
    ]
    confirmation = [
        candle for candle in confirmation_all
        if start_ts - warmup <= candle["timestamp"] <= end_ts
    ]
    trend = [
        candle for candle in trend_all
        if start_ts - warmup <= candle["timestamp"] <= end_ts
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
                trade.update(position.get("diagnostics", {}))
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

            if invalid or age > int(PROFILE["setup_expiry_candles"]):
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
                        PROFILE,
                        timestamp,
                    )
                    diagnostics = pending["diagnostics"]
                    pending = None

                    if position is not None:
                        position["diagnostics"] = diagnostics
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
            PROFILE,
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

        risk_distance = abs(
            float(setup["entry"])
            - float(setup["stop"])
        )

        atr_value = float(indicators.get("atr") or 0.0)
        adx_value = float(indicators.get("adx") or 0.0)
        ema_value = float(indicators.get("ema200") or 0.0)

        pending = {
            "setup": setup,
            "created_index": index,
            "diagnostics": {
                "setup_id": setup_id,
                "signal_timestamp": int(timestamp),
                "adx": adx_value,
                "atr": atr_value,
                "ema200": ema_value,
                "risk_distance": risk_distance,
                "risk_atr_ratio": (
                    risk_distance / atr_value
                    if atr_value > 0
                    else 0.0
                ),
                "fvg_size": abs(
                    float(setup["fvg_upper"])
                    - float(setup["fvg_lower"])
                ),
                "fvg_atr_ratio": (
                    abs(
                        float(setup["fvg_upper"])
                        - float(setup["fvg_lower"])
                    ) / atr_value
                    if atr_value > 0
                    else 0.0
                ),
            },
        }

    if position is not None and entry:
        last_candle = entry[-1]
        last_price = float(last_candle["close"])
        close_part(
            position,
            last_price,
            position["remaining"],
            "WINDOW_END",
        )
        trade = finalize(
            position,
            last_candle["timestamp"],
        )
        trade.update(position.get("diagnostics", {}))
        trades.append(trade)

    return trades


def side_summary(trades, side):
    subset = [t for t in trades if t["side"] == side]
    result = stats(subset)
    return result


def group_by_threshold(trades, field, threshold, label_low, label_high):
    low = [t for t in trades if float(t.get(field, 0.0)) < threshold]
    high = [t for t in trades if float(t.get(field, 0.0)) >= threshold]
    return [
        (label_low, stats(low)),
        (label_high, stats(high)),
    ]


def block_ranges(one_minute):
    end_ts = one_minute[-1]["timestamp"]
    block_ms = BLOCK_DAYS * 24 * 60 * 60 * 1000
    available_ms = end_ts - one_minute[0]["timestamp"]
    max_blocks = max(1, int(available_ms // block_ms))
    count = min(BLOCK_COUNT, max_blocks)

    blocks = []
    for block_number in range(count, 0, -1):
        block_end = end_ts - (block_number - 1) * block_ms
        block_start = block_end - block_ms + 60_000
        blocks.append((block_start, block_end))
    return blocks


def print_stats(label, result):
    print(
        label,
        "| Trades:", result["trades"],
        "| L/S:", f'{result["longs"]}/{result["shorts"]}',
        "| Treffer:", f'{result["win_rate"]:.1f}%',
        "| PF:", pf_text(result["pf"]),
        "| Netto:", f'{result["net"]:.2f}',
    )


def main():
    one_minute = load_csv(SYMBOL, "1m")
    blocks = block_ranges(one_minute)

    print("ADA V8 EXPIRY2 – VERLUSTBLOCK-DIAGNOSE")
    print("Nur Analyse, keine Orders, keine Live-Änderung.")
    print("Untersucht werden Block 5 und Block 6.")

    for block_index in sorted(TARGET_BLOCKS):
        start_ts, end_ts = blocks[block_index - 1]
        trades = run_window_detailed(start_ts, end_ts)

        print("")
        print("================================")
        print("BLOCK", block_index)
        print("================================")
        print_stats("GESAMT", stats(trades))
        print_stats("LONG ", side_summary(trades, "LONG"))
        print_stats("SHORT", side_summary(trades, "SHORT"))

        adx_values = sorted(float(t["adx"]) for t in trades)
        fvg_values = sorted(float(t["fvg_atr_ratio"]) for t in trades)
        risk_values = sorted(float(t["risk_atr_ratio"]) for t in trades)

        if adx_values:
            median_adx = adx_values[len(adx_values) // 2]
            print("")
            print("ADX-SPLIT bei Median", f"{median_adx:.2f}")
            for label, result in group_by_threshold(
                trades,
                "adx",
                median_adx,
                "ADX niedrig",
                "ADX hoch",
            ):
                print_stats(label, result)

        if fvg_values:
            median_fvg = fvg_values[len(fvg_values) // 2]
            print("")
            print("FVG/ATR-SPLIT bei Median", f"{median_fvg:.3f}")
            for label, result in group_by_threshold(
                trades,
                "fvg_atr_ratio",
                median_fvg,
                "FVG klein ",
                "FVG gross ",
            ):
                print_stats(label, result)

        if risk_values:
            median_risk = risk_values[len(risk_values) // 2]
            print("")
            print("RISK/ATR-SPLIT bei Median", f"{median_risk:.3f}")
            for label, result in group_by_threshold(
                trades,
                "risk_atr_ratio",
                median_risk,
                "Risk klein",
                "Risk gross",
            ):
                print_stats(label, result)

        losers = sorted(
            [t for t in trades if t["net_pnl"] < 0],
            key=lambda t: t["net_pnl"],
        )

        print("")
        print("5 GROESSTE VERLUST-TRADES")
        for trade in losers[:5]:
            print(
                trade["side"],
                "| PnL", f'{trade["net_pnl"]:.2f}',
                "| ADX", f'{trade["adx"]:.2f}',
                "| FVG/ATR", f'{trade["fvg_atr_ratio"]:.3f}',
                "| Risk/ATR", f'{trade["risk_atr_ratio"]:.3f}',
            )

    print("")
    print(
        "Hinweis: Diese Diagnose dient dazu, mögliche "
        "Merkmale schwacher Trades zu finden. Filter "
        "müssen danach separat über alle Blöcke und OOS "
        "validiert werden."
    )


if __name__ == "__main__":
    main()
