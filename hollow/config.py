"""Settings from environment variables, with an optional .env file at the repo root."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


_load_dotenv()

MQTT_HOST = os.environ.get("MQTT_HOST", "localhost")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME") or None
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD") or None
MQTT_TLS = os.environ.get("MQTT_TLS", "0") == "1"
GATEWAY_ID = os.environ.get("GATEWAY_ID", "gw-local")

# HOLLOW_KEYS_DIR points everything at another key set (tools/build_public.py uses throwaway keys).
KEYS_DIR = Path(os.environ["HOLLOW_KEYS_DIR"]) if os.environ.get("HOLLOW_KEYS_DIR") else REPO_ROOT / "keys"
TRUSTED_KEYS_FILE = KEYS_DIR / "trusted.json"
