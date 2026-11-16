"""Ed25519 signed envelopes.

The signer serializes the message to a JSON string ONCE, signs those exact UTF-8
bytes, and ships the string untouched inside the envelope. Verifiers check the
signature over the string first, then parse it. Nothing is ever re-serialized,
so Python and JavaScript never need to agree on float formatting or key order.
"""

import base64
import json
import time
from pathlib import Path

from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

from . import config


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def generate_key(gateway_id: str) -> str:
    """Create keys/<gateway_id>.key (private) and add the public key to keys/trusted.json."""
    config.KEYS_DIR.mkdir(exist_ok=True)
    key_path = config.KEYS_DIR / f"{gateway_id}.key"
    if key_path.exists():
        signing_key = load_signing_key(gateway_id)
    else:
        signing_key = SigningKey.generate()
        key_path.write_text(b64(bytes(signing_key)), encoding="ascii")

    public = b64(bytes(signing_key.verify_key))
    trusted = load_trusted_keys()
    trusted[gateway_id] = public
    config.TRUSTED_KEYS_FILE.write_text(json.dumps(trusted, indent=2) + "\n", encoding="utf-8")
    return public


def load_signing_key(gateway_id: str) -> SigningKey:
    key_path = config.KEYS_DIR / f"{gateway_id}.key"
    if not key_path.exists():
        raise FileNotFoundError(
            f"No key for {gateway_id}. Run: python -m tools.keygen {gateway_id}"
        )
    return SigningKey(base64.b64decode(key_path.read_text(encoding="ascii").strip()))


def load_trusted_keys(path: Path | None = None) -> dict[str, str]:
    path = path or config.TRUSTED_KEYS_FILE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


class TrustStore:
    """Trusted public keys, reloaded from keys/trusted.json when an unknown signer shows up
    (so adding a teammate's gateway or the operator key doesn't need a restart)."""

    def __init__(self):
        self.reload()

    def reload(self) -> None:
        self.keys = load_trusted_keys()
        self.loaded = time.time()

    def open(self, envelope: dict, role: str | None = None) -> dict:
        if envelope.get("gateway_id") not in self.keys and time.time() - self.loaded > 5:
            self.reload()
        return open_envelope(envelope, self.keys, role)


def seal(signer_id: str, signing_key: SigningKey, message: dict) -> dict:
    """Wrap a message in a signed envelope: {gateway_id, payload, signature}."""
    payload = json.dumps(message, separators=(",", ":"), ensure_ascii=False)
    signature = signing_key.sign(payload.encode("utf-8")).signature
    return {"gateway_id": signer_id, "payload": payload, "signature": b64(signature)}


# Signer roles, by key name in keys/trusted.json. Each kind of message is only accepted
# from its role: a gateway key can't approve commands, the operator key can't forge readings.
VERIFIER = "verifier"
OPERATOR = "operator"
SPECIAL = {VERIFIER, OPERATOR}


def role_of(signer: str) -> str:
    return signer if signer in SPECIAL else "gateway"


def open_envelope(envelope: dict, trusted: dict[str, str], role: str | None = None) -> dict:
    """Verify an envelope against trusted public keys and return the parsed message.
    If role is given ("gateway", "verifier", "operator"), the signer must have that role.

    Raises ValueError with a short reason when the envelope is rejected.
    """
    signer = envelope.get("gateway_id")
    public = trusted.get(signer)
    if public is None:
        raise ValueError(f"untrusted signer {signer!r}")
    if role and role_of(signer) != role:
        raise ValueError(f"{signer!r} may not sign this kind of message")
    try:
        VerifyKey(base64.b64decode(public)).verify(
            envelope["payload"].encode("utf-8"), base64.b64decode(envelope["signature"])
        )
    except (BadSignatureError, KeyError, ValueError) as exc:
        raise ValueError("bad signature") from exc
    message = json.loads(envelope["payload"])
    if message.get("gateway_id") != signer:
        raise ValueError("payload gateway_id does not match signer")
    return message
