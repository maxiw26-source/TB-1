import json
import os
from pathlib import Path

from live_partial_tp import (
    calculate_partial_quantities,
    get_pending_tpsl_orders,
)
from bitunix_live import (
    get_pending_positions,
    get_trading_pair,
)

ENV_FILE = Path(".env")
SYMBOLS = ["BTCUSDT", "ETHUSDT"]


def load_env_file():
    if not ENV_FILE.exists():
        return

    for raw_line in ENV_FILE.read_text(
        encoding="utf-8"
    ).splitlines():
        line = raw_line.strip()

        if (
            not line
            or line.startswith("#")
            or "=" not in line
        ):
            continue

        key, value = line.split("=", 1)

        key = key.strip()
        value = (
            value.strip()
            .strip('"')
            .strip("'")
        )

        if key and key not in os.environ:
            os.environ[key] = value


def safe_position(item):
    return {
        "positionId": item.get("positionId"),
        "symbol": item.get("symbol"),
        "qty": item.get("qty"),
        "side": item.get("side"),
        "positionMode": item.get("positionMode"),
        "entryPrice": (
            item.get("entryPrice")
            or item.get("avgOpenPrice")
            or item.get("avgPrice")
        ),
    }


def safe_tpsl(item):
    return {
        "id": item.get("id"),
        "positionId": item.get("positionId"),
        "symbol": item.get("symbol"),
        "tpPrice": item.get("tpPrice"),
        "slPrice": item.get("slPrice"),
        "tpQty": item.get("tpQty"),
        "slQty": item.get("slQty"),
    }


def main():
    load_env_file()

    print("LIVE PARTIALS READ-ONLY PREFLIGHT")
    print("Es werden KEINE Orders gesendet oder geändert.")

    report = {}

    for symbol in SYMBOLS:
        rules = get_trading_pair(symbol)
        positions = get_pending_positions(symbol)

        symbol_report = {
            "rules": {
                "minTradeVolume": rules.get("minTradeVolume"),
                "basePrecision": rules.get("basePrecision"),
                "quotePrecision": rules.get("quotePrecision"),
            },
            "positions": [
                safe_position(item)
                for item in positions
            ],
            "pending_tpsl": [],
            "partial_70_30": None,
        }

        for position in positions:
            position_id = position.get("positionId")

            orders = get_pending_tpsl_orders(
                symbol=symbol,
                position_id=position_id,
            )

            symbol_report["pending_tpsl"].extend(
                safe_tpsl(item)
                for item in orders
            )

            try:
                quantities = calculate_partial_quantities(
                    position.get("qty"),
                    70,
                    int(rules.get("basePrecision", 0)),
                    rules.get("minTradeVolume", "0"),
                )

                symbol_report["partial_70_30"] = {
                    "compatible": True,
                    **quantities,
                }
            except Exception as exc:
                symbol_report["partial_70_30"] = {
                    "compatible": False,
                    "reason": str(exc),
                }

        report[symbol] = symbol_report

    print(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        )
    )

    print("")
    print(
        "PREFLIGHT FERTIG – keine Order wurde gesendet."
    )


if __name__ == "__main__":
    main()
