"""Shared test helpers: throwaway signing keys and a fake MQTT client.

Tests never read or write keys/: KeyRing patches the key loaders, so they pass on a fresh
clone (and in CI) without running tools.keygen, and never touch a teammate's real keys.
"""

import json
import time
import types
import unittest
from unittest import mock

from nacl.signing import SigningKey

from hollow import replay, signing
from hollow.mqtt import now_iso

GATEWAY = "gw-test"


class KeyRing:
    """Fresh keys for a gateway, the verifier and the operator, trusted for the duration of a test."""

    def __init__(self, gateway: str = GATEWAY):
        self.gateway = gateway
        self.keys = {name: SigningKey.generate() for name in (gateway, signing.VERIFIER, signing.OPERATOR)}
        self.trusted = {name: signing.b64(bytes(k.verify_key)) for name, k in self.keys.items()}
        self.stamper = replay.Stamper()

    def install(self, test: unittest.TestCase) -> None:
        for target, fake in (
            ("hollow.signing.load_signing_key", lambda name: self.keys[name]),
            ("hollow.signing.load_trusted_keys", lambda path=None: dict(self.trusted)),
        ):
            patcher = mock.patch(target, side_effect=fake)
            patcher.start()
            test.addCleanup(patcher.stop)

    def seal(self, signer: str, message: dict, key: SigningKey | None = None, stamp: bool = True) -> dict:
        """Sign a message as `signer` (with its real key unless `key` is given), adding boot/seq/timestamp."""
        message = {"gateway_id": signer, "timestamp": now_iso(), **message}
        if stamp:
            self.stamper.stamp(message)
        return signing.seal(signer, key or self.keys[signer], message)


class FakeClient:
    """Stands in for a paho client: records every publish."""

    def __init__(self):
        self.published: list[tuple[str, str, bool]] = []

    def publish(self, topic, payload=None, retain=False, **kwargs):
        self.published.append((topic, payload, retain))
        return types.SimpleNamespace(wait_for_publish=lambda *a, **k: None, rc=0)

    def subscribe(self, *args, **kwargs):
        return (0, 1)

    def on(self, prefix: str) -> list:
        """Payloads (parsed when JSON) published on topics starting with prefix."""
        out = []
        for topic, payload, _ in self.published:
            if topic.startswith(prefix):
                try:
                    out.append(json.loads(payload))
                except (TypeError, json.JSONDecodeError):
                    out.append(payload)
        return out


def message(topic: str, payload, retain: bool = False):
    """A paho-like MQTTMessage."""
    body = payload if isinstance(payload, (bytes, str)) else json.dumps(payload)
    return types.SimpleNamespace(topic=topic, payload=body.encode() if isinstance(body, str) else body, retain=retain)


def epoch_iso(seconds_ago: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds_ago))
