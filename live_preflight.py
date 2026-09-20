import json
import os
from pathlib import Path

from bitunix_live import (
    LiveExecutionError,
    get_pending_orders,
    get_pending_positions,
    get_ticker,
    get_trading_pair,
    live_execution_status,
)


def load_env_file(path=".env"):
    env_path = Path(path)

    if not env_path.exists():
        return

    for raw_line in env_path.read_text(
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


def main():
    load_env_file()

    report = {
        "config": (
            live_execution_status()
        ),
        "symbols": {},
    }

    for symbol in [
        "BTCUSDT",
        "ETHUSDT",
    ]:
        rules = get_trading_pair(
            symbol
        )
        ticker = get_ticker(
            symbol
        )

        report["symbols"][symbol] = {
            "symbolStatus": (
                rules.get(
                    "symbolStatus"
                )
            ),
            "isApiSupported": (
                rules.get(
                    "isApiSupported"
                )
            ),
            "minTradeVolume": (
                rules.get(
                    "minTradeVolume"
                )
            ),
            "basePrecision": (
                rules.get(
                    "basePrecision"
                )
            ),
            "quotePrecision": (
                rules.get(
                    "quotePrecision"
                )
            ),
            "lastPrice": (
                ticker.get(
                    "lastPrice"
                )
                or ticker.get(
                    "last"
                )
            ),
        }

    report[
        "pending_positions_count"
    ] = len(
        get_pending_positions()
    )

    report[
        "pending_orders_count"
    ] = len(
        get_pending_orders()
    )

    report["account_balance_check"] = (
        "not_queried"
    )

    print(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        )
    )

    print("")
    print(
        "PREFLIGHT ERFOLGREICH – "
        "es wurde keine Order gesendet."
    )


if __name__ == "__main__":
    try:
        main()
    except LiveExecutionError as exc:
        print(
            "PREFLIGHT FEHLER:",
            exc,
        )
        raise SystemExit(1)
