"""MQTT topic names. Must match the data contract in the project outline doc."""


def raw(gateway_id: str, protocol: str, unit_id: str) -> str:
    return f"surface/raw/{gateway_id}/{protocol}/{unit_id}"


def raw_wildcard(gateway_id: str, protocol: str = "+") -> str:
    return f"surface/raw/{gateway_id}/{protocol}/+"


DISCOVERED = "uplink/discovered"


def reading(zone: str, unit_id: str) -> str:
    return f"uplink/readings/{zone}/{unit_id}"


READINGS_ALL = "uplink/readings/#"


def zone_score(zone: str) -> str:
    return f"uplink/zones/{zone}/score"


MAP_SNAPSHOT = "uplink/map/snapshot"


def key(gateway_id: str) -> str:
    return f"uplink/keys/{gateway_id}"


def command(unit_id: str) -> str:
    return f"uplink/commands/{unit_id}"


def ack(unit_id: str) -> str:
    return f"uplink/acks/{unit_id}"


KILLSWITCH = "uplink/control/killswitch"
COMMANDS_ALL = "uplink/commands/+"
INVESTIGATIONS = "uplink/investigations"   # retained, signed list of recent investigations


def machine_command(gateway_id: str, protocol: str, unit_id: str) -> str:
    """Gateway -> machine, in the machine's own format (the 'down' half of an adapter)."""
    return f"surface/cmd/{gateway_id}/{protocol}/{unit_id}"


def sim_control(gateway_id: str) -> str:
    """Simulation-only control channel (e.g. compromising a sensor for the demo).
    Not part of the real system: real machines have no such topic."""
    return f"sim/control/{gateway_id}"
