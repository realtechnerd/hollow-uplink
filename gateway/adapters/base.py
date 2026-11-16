from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class Fingerprint:
    kind: str          # e.g. "air_sensor", "scout_robot"; "unknown" if no rule matched
    confidence: float  # 0..1
    evidence: str      # human-readable reason, shown on the dashboard


class Adapter(Protocol):
    protocol: str

    def fingerprint(self, raw: Any) -> Fingerprint:
        """Guess what kind of machine produced this raw message."""
        ...

    def translate(self, kind: str, raw: Any) -> dict[str, float]:
        """Turn a raw message into common-format readings (units in the key names)."""
        ...

    def extras(self, kind: str, raw: Any) -> dict[str, Any]:
        """Optional extra common-format fields: "location" for machines that move,
        "survey" for machines that map their surroundings. Return {} if none."""
        ...

    def command(self, kind: str, cmd: dict) -> dict | None:
        """Translate an approved common-format command into this machine's own format,
        or None if this kind of machine can't do it."""
        ...
