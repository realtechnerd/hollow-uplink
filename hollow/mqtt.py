"""One place to build a paho-mqtt client from config."""

import ssl
import uuid
from datetime import datetime, timezone

import paho.mqtt.client as paho

from . import config


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def make_client(name: str) -> paho.Client:
    client = paho.Client(
        paho.CallbackAPIVersion.VERSION2,
        client_id=f"{name}-{uuid.uuid4().hex[:6]}",
    )
    if config.MQTT_USERNAME:
        client.username_pw_set(config.MQTT_USERNAME, config.MQTT_PASSWORD)
    if config.MQTT_TLS:
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)
    client.reconnect_delay_set(min_delay=1, max_delay=10)
    return client


def connect(client: paho.Client) -> None:
    client.connect(config.MQTT_HOST, config.MQTT_PORT, keepalive=30)
