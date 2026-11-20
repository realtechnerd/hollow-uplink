"""Demo attacks on running simulated sensors (simulation only).

    python -m tools.attack compromise mqtt-rad-04 --value 8      # Directorate hijacks a probe
    python -m tools.attack restore mqtt-rad-04

A compromised sensor tells a believable lie: plausible values with natural jitter,
passed through the real gateway and validly signed. Signatures can't catch it;
the verifier's investigation (sending an independent machine to check) does.
For forged or tampered messages, see tools/spoof.py instead.
"""

import argparse
import json
import time

from hollow import config, topics
from hollow.mqtt import connect, make_client


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["compromise", "restore"])
    parser.add_argument("unit_id")
    parser.add_argument("--value", type=float, default=8.0, help="value the sensor should claim")
    parser.add_argument("--gateway", default=config.GATEWAY_ID)
    args = parser.parse_args()

    ctl = {"unit_id": args.unit_id, "mode": "compromised" if args.action == "compromise" else "healthy"}
    if args.action == "compromise":
        ctl["value"] = args.value

    client = make_client("attacker")
    connect(client)
    client.loop_start()
    client.publish(topics.sim_control(args.gateway), json.dumps(ctl)).wait_for_publish()
    time.sleep(0.3)  # like tools/operator.py: a busy dev broker can drop a message sent right before a disconnect
    client.loop_stop()
    client.disconnect()
    print(f"{args.action}: {args.unit_id}" + (f" now claims {args.value}" if args.action == "compromise" else ""))


if __name__ == "__main__":
    main()
