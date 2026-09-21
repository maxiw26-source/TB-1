import os


AUTO_ACK = "I_UNDERSTAND_UNATTENDED_REAL_ORDERS"


def _env_true(name, default="false"):
    return os.getenv(
        name,
        default,
    ).strip().lower() in {
        "1",
        "true",
        "yes",
        "ja",
        "on",
    }


def auto_live_status():
    enabled = _env_true(
        "ENABLE_AUTO_LIVE_EXECUTION",
        "false",
    )

    armed_ack = (
        os.getenv(
            "AUTO_LIVE_EXECUTION_ACK",
            "",
        ).strip()
        == AUTO_ACK
    )

    return {
        "enabled": enabled,
        "armed_ack": armed_ack,
        "active": bool(
            enabled
            and armed_ack
        ),
    }


def auto_live_active():
    return bool(
        auto_live_status()["active"]
    )
