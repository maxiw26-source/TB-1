"""Retrospective ADA V8 FVG-filter walk-forward (research only).

All three candidates use balanced + expiry2. At each test block the
candidate is selected using only earlier blocks. These historical periods
were already inspected in prior research: this is NOT fresh OOS evidence.
"""

from backtest_ada_v8_targeted_filter import (
    SYMBOL,
    VARIANTS,
    aggregate_pf,
    blocks,
    pf_text,
    run_window_filtered,
)
from backtest_v8_walkforward import load_csv

CANDIDATE_NAMES = (
    "expiry2_base",
    "long_fvg_015",
    "long_fvg_025",
)
MIN_TRAIN_BLOCKS = 3


def summarize(results):
    profit = sum(max(0.0, x["net"]) for x in results)
    loss = abs(sum(min(0.0, x["net"]) for x in results))
    return {
        "blocks": len(results),
        "positive": sum(1 for x in results if x["trades"] and x["net"] > 0),
        "trades": sum(x["trades"] for x in results),
        "net": sum(x["net"] for x in results),
        # Consistent with earlier reports: PF from block-level net PnL,
        # not the trade-level gross-profit / gross-loss ratio.
        "block_pf": profit / loss if loss else (float("inf") if profit else 0.0),
    }


def select_from_past(prior):
    """Predeclared criterion: positive blocks, then block-PF, then net."""
    ranked = []
    for name in CANDIDATE_NAMES:
        s = summarize(prior[name])
        ranked.append(((s["positive"], s["block_pf"], s["net"]), name))
    ranked.sort(reverse=True)
    return ranked[0][1]


def main():
    history = load_csv(SYMBOL, "1m")
    windows = blocks(history)
    variants = {item["name"]: item for item in VARIANTS}
    if len(windows) <= MIN_TRAIN_BLOCKS:
        raise RuntimeError("Nicht genug 90-Tage-Bloecke fuer Walk-Forward")

    print("ADA V8 FVG WALK-FORWARD – BALANCED + EXPIRY2", flush=True)
    print("Kandidaten: " + ", ".join(CANDIDATE_NAMES), flush=True)
    print("Auswahl: positive Trainingsbloecke, dann Block-PF, dann Netto", flush=True)
    print("Nur Recherche; keine Orders oder Live-Aenderungen.", flush=True)

    cache = {}
    for name in CANDIDATE_NAMES:
        print(f"Berechne {name} ...", flush=True)
        cache[name] = [
            run_window_filtered(start, end, variants[name])
            for start, end in windows
        ]

    adaptive = []
    chosen_names = []
    start_index = MIN_TRAIN_BLOCKS

    print("\nZEITLICHER WALK-FORWARD", flush=True)
    for index in range(start_index, len(windows)):
        prior = {name: cache[name][:index] for name in CANDIDATE_NAMES}
        chosen = select_from_past(prior)
        chosen_names.append(chosen)
        result = cache[chosen][index]
        adaptive.append(result)
        print(
            f"Block {index + 1} | gewaehlt {chosen}"
            f" | Trades {result['trades']}"
            f" | L/S {result['longs']}/{result['shorts']}"
            f" | PF {pf_text(result['pf'])}"
            f" | Netto {result['net']:+.2f} USDT",
            flush=True,
        )

    print("\nVERGLEICH AUF DENSELBEN TESTBLOECKEN", flush=True)
    comparisons = {
        "adaptive_walk_forward": adaptive,
        **{name: cache[name][start_index:] for name in CANDIDATE_NAMES},
    }
    for name, results in comparisons.items():
        s = summarize(results)
        print(
            f"{name} | Positive {s['positive']}/{s['blocks']}"
            f" | Trades {s['trades']}"
            f" | Block-PF {pf_text(s['block_pf'])}"
            f" | Netto {s['net']:+.2f} USDT",
            flush=True,
        )
    print("Gewaehlt: " + ", ".join(chosen_names), flush=True)
    print(
        "\nACHTUNG: Die historischen Testperioden und Kandidaten wurden"
        " bereits in frueheren Analysen betrachtet. Dies ist eine"
        " retrospektive zeitliche Simulation, KEIN neuer unabhaengiger"
        " Out-of-Sample-Test. Fuer echte neue Evidenz ist zusaetzlich"
        " vorab festgelegtes Paper-Forward-Testing erforderlich.",
        flush=True,
    )


if __name__ == "__main__":
    main()
