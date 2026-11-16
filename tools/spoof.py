"""Demo attacks on signed messages. All three are rejected by the dashboard and verifier.

    python -m tools.spoof forged   mqtt-rad-02 --zone Z1   # signed with a key nobody trusts
    python -m tools.spoof tampered mqtt-rad-02 --zone Z1   # a real gateway's envelope, edited after signing
    python -m tools.spoof replayed mqtt-rad-02             # a genuine signed reading, recorded and re-sent

"forged" impersonates the gateway with a fresh random key.
"tampered" needs the real gateway's key file locally, to stand in for a reading
intercepted in transit and edited (radiation set to a lethal value).
"replayed" records a real, validly signed reading off the broker and sends the exact
same bytes again a few seconds later. The signature is genuine; the sequence number gives it away.
"""

import argparse
import json
import threading
import time

from nacl.signing import SigningKey

from hollow import config, replay, signing, topics
from hollow.mqtt import connect, make_client, now_iso


def build_reading(args) -> dict:
    return replay.Stamper().stamp({
        "unit_id": args.unit_id,
        "gateway_id": args.gateway,
        "protocol": "mqtt",
        "kind": "radiation_probe",
        "zone": args.zone,
        "location": None,
        "timestamp": now_iso(),
        "readings": {"radiation_usv_h": 0.2},
        "status": "online",
    })


def record_one(client, unit_id: str, timeout: float = 15) -> tuple[str, bytes]:
    """Capture one genuine signed reading for unit_id off the broker."""
    got: dict = {}
    done = threading.Event()

    def on_message(c, u, msg):
        # Check the topic: some brokers (amqtt) also deliver unrelated retained messages on subscribe.
        parts = msg.topic.split("/")
        if len(parts) != 4 or parts[:2] != ["uplink", "readings"] or parts[3] != unit_id or msg.retain:
            return
        if not done.is_set():
            got["topic"], got["payload"] = msg.topic, msg.payload
            done.set()

    client.on_message = on_message
    client.subscribe(f"uplink/readings/+/{unit_id}")
    if not done.wait(timeout):
        raise SystemExit(f"No reading from {unit_id} within {timeout:.0f} s. Is it running?")
    client.unsubscribe(f"uplink/readings/+/{unit_id}")
    return got["topic"], got["payload"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["forged", "tampered", "replayed"])
    parser.add_argument("unit_id")
    parser.add_argument("--zone", default="Z1")
    parser.add_argument("--gateway", default=config.GATEWAY_ID)
    parser.add_argument("--delay", type=float, default=3, help="replayed: seconds between recording and re-sending")
    args = parser.parse_args()

    client = make_client("spoofer")
    connect(client)
    client.loop_start()

    if args.mode == "replayed":
        topic, payload = record_one(client, args.unit_id)
        print(f"Recorded a genuine signed reading from {args.unit_id}; re-sending it in {args.delay:g} s")
        time.sleep(args.delay)
    else:
        reading = build_reading(args)
        if args.mode == "forged":
            envelope = signing.seal(args.gateway, SigningKey.generate(), {**reading, "readings": {"radiation_usv_h": 950.0}})
        else:
            envelope = signing.seal(args.gateway, signing.load_signing_key(args.gateway), reading)
            tampered = json.loads(envelope["payload"])
            tampered["readings"]["radiation_usv_h"] = 950.0
            envelope["payload"] = json.dumps(tampered, separators=(",", ":"))
        topic, payload = topics.reading(args.zone, args.unit_id), json.dumps(envelope)

    client.publish(topic, payload).wait_for_publish()
    client.loop_stop()
    client.disconnect()
    print(f"Sent {args.mode} reading for {args.unit_id}")


if __name__ == "__main__":
    main()
