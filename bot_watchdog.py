import json
import os
import subprocess
from pathlib import Path

import requests

STATE_FILE = Path("/root/TB-1/watchdog_state.json")
ENV_FILE = Path("/root/TB-1/.env")
SERVICE = os.getenv("LSOB_SERVICE", "lsob-v7")


def load_env_file():
    if not ENV_FILE.exists():
        return
    for raw_line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def send_message(text):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False

    response = requests.post(
        f"https://api.telegram.org/bot{token}/sendMessage",
        json={"chat_id": str(chat_id), "text": text},
        timeout=10,
    )
    response.raise_for_status()
    payload = response.json()
    return bool(payload.get("ok"))


def load_state():
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def boot_id():
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text(
            encoding="utf-8"
        ).strip()
    except Exception:
        return "unknown"


def service_active():
    result = subprocess.run(
        ["systemctl", "is-active", "--quiet", SERVICE],
        check=False,
    )
    return result.returncode == 0


def restart_service():
    subprocess.run(
        ["systemctl", "restart", SERVICE],
        check=False,
        timeout=30,
    )
    return service_active()


def main():
    load_env_file()
    state = load_state()
    current_boot = boot_id()

    if state.get("boot_id") != current_boot:
        try:
            send_message(
                "LSOB V7 Watchdog: VPS ist gestartet und die Überwachung ist aktiv."
            )
        except Exception:
            pass
        state["boot_id"] = current_boot

    active = service_active()
    previous_active = state.get("service_active")

    if not active:
        recovered = restart_service()
        if recovered:
            try:
                send_message(
                    "LSOB V7 Watchdog: Bot-Dienst war ausgefallen und wurde automatisch neu gestartet."
                )
            except Exception:
                pass
            active = True
        else:
            if previous_active is not False:
                try:
                    send_message(
                        "LSOB V7 Watchdog WARNUNG: Bot-Dienst ist ausgefallen und der automatische Neustart ist fehlgeschlagen."
                    )
                except Exception:
                    pass
            active = False
    elif previous_active is False:
        try:
            send_message(
                "LSOB V7 Watchdog: Bot-Dienst ist wieder online."
            )
        except Exception:
            pass

    state["service_active"] = active
    save_state(state)


if __name__ == "__main__":
    main()
