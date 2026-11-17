"""Protocol adapters. Adding a protocol = adding one module here and registering it."""

from .base import Adapter, Fingerprint
from .modbus_adapter import ModbusAdapter
from .mqtt_adapter import MqttAdapter

ADAPTERS: dict[str, Adapter] = {"mqtt": MqttAdapter(), "modbus": ModbusAdapter()}

__all__ = ["ADAPTERS", "Adapter", "Fingerprint"]
