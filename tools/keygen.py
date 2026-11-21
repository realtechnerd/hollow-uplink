"""Create a gateway's signing key and register its public key as trusted.

    python -m tools.keygen gw-va

Writes keys/gw-va.key (private, git-ignored) and adds the public key to
keys/trusted.json, which IS committed so the dashboard and verifier trust it.
"""

import sys

from hollow import config, signing


def main() -> None:
    gateway_id = sys.argv[1] if len(sys.argv) > 1 else config.GATEWAY_ID
    public = signing.generate_key(gateway_id)
    print(f"{gateway_id} public key: {public}")
    if config.TRUSTED_KEYS_FILE.is_relative_to(config.REPO_ROOT / "keys"):
        print(f"Commit {config.TRUSTED_KEYS_FILE.relative_to(config.REPO_ROOT)} so others trust it.")


if __name__ == "__main__":
    main()
