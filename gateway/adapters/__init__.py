"""Protocol adapters. Adding a protocol = adding one module here and registering it."""

from .base import Adapter, Fingerprint
from .mqtt_adapter import MqttAdapter

ADAPTERS: dict[str, Adapter] = {"mqtt": MqttAdapter()}

__all__ = ["ADAPTERS", "Adapter", "Fingerprint"]
