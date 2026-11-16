"""Replay protection for signed messages.

A signature proves who wrote a message, not when. Without this, anyone who recorded a
genuine signed reading could send it again later and have it accepted. So every signed
message carries:

    "boot":      random id chosen when the sending service starts
    "seq":       counter that only goes up within one boot
    "timestamp": when it was written (UTC)

and receivers keep the last (boot, seq) per stream (one unit's readings, one topic).
A message is rejected if it is stale, from the future, or not newer than the last one
seen on its stream. Clocks across teammates' machines should be within MAX_SKEW_S.
"""

import itertools
import uuid
from datetime import datetime, timezone

MAX_AGE_S = 120
MAX_SKEW_S = 120


class Stamper:
    """Adds boot / seq to outgoing messages for one sending service."""

    def __init__(self):
        self.boot = uuid.uuid4().hex[:8]
        self._seq = itertools.count(1)

    def stamp(self, message: dict) -> dict:
        message["boot"] = self.boot
        message["seq"] = next(self._seq)
        return message


def _epoch(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


class ReplayGuard:
    def __init__(self, max_age_s: float = MAX_AGE_S):
        self.max_age_s = max_age_s
        self.last: dict[str, tuple[str, int]] = {}

    def check(self, stream: str, message: dict, now: float | None = None, retained: bool = False) -> bool:
        """True if the message is new. False if it's a harmless duplicate of a retained message
        (the broker re-sends those on reconnect). Raises ValueError for replays and stale messages."""
        now = now if now is not None else datetime.now(timezone.utc).timestamp()
        try:
            age = now - _epoch(message["timestamp"])
            boot, seq = message["boot"], int(message["seq"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("missing timestamp or sequence number") from exc
        prev = self.last.get(stream)
        if prev and prev[0] == boot and seq == prev[1] and retained:
            return False
        if age > self.max_age_s:
            raise ValueError(f"stale: written {age:.0f} s ago (possible replay)")
        if age < -MAX_SKEW_S:
            raise ValueError("timestamp is in the future")
        if prev and prev[0] == boot and seq <= prev[1]:
            raise ValueError(f"replayed: sequence {seq} already seen")
        self.last[stream] = (boot, seq)
        return True
