"""Demo attacks the system should catch. Both show up as "rejected" on the dashboard.

    python -m tools.spoof forged  mqtt-rad-02 --zone Z1    # signed with a key nobody trusts
    python -m tools.spoof tampered mqtt-rad-02 --zone Z1   # a real gateway's envelope, payload edited after signing

"forged" impersonates the gateway with a fresh random key.
"tampered" needs the real gateway's key file locally, to stand in for a
reading intercepted in transit and edited (radiation set to a lethal value).
"""

import argparse
import json

from nacl.signing import SigningKey

from hollow import config, signing, topics
from hollow.mqtt import connect, make_client, now_iso


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["forged", "tampered"])
    parser.add_argument("unit_id")
    parser.add_argument("--zone", default="Z1")
    parser.add_argument("--gateway", default=config.GATEWAY_ID)
    args = parser.parse_args()

    reading = {
        "unit_id": args.unit_id,
        "gateway_id": args.gateway,
        "protocol": "mqtt",
        "kind": "radiation_probe",
        "zone": args.zone,
        "location": None,
        "timestamp": now_iso(),
        "readings": {"radiation_usv_h": 0.2},
        "status": "online",
    }

    if args.mode == "forged":
        envelope = signing.seal(args.gateway, SigningKey.generate(), {**reading, "readings": {"radiation_usv_h": 950.0}})
    else:
        envelope = signing.seal(args.gateway, signing.load_signing_key(args.gateway), reading)
        tampered = json.loads(envelope["payload"])
        tampered["readings"]["radiation_usv_h"] = 950.0
        envelope["payload"] = json.dumps(tampered, separators=(",", ":"))

    client = make_client("spoofer")
    connect(client)
    client.loop_start()
    client.publish(topics.reading(args.zone, args.unit_id), json.dumps(envelope)).wait_for_publish()
    client.loop_stop()
    client.disconnect()
    print(f"Sent {args.mode} reading for {args.unit_id}")


if __name__ == "__main__":
    main()
