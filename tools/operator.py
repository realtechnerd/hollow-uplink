"""Operator actions from the command line, signed with keys/operator.key.
Same messages the 3D view sends in operator mode; handy for scripting a demo.

    python -m tools.operator investigate inv-0001 --unit scout-02 --x 470 --y 420
    python -m tools.operator killswitch on
    python -m tools.operator killswitch off

Create the key once with: python -m tools.keygen operator
"""

import argparse
import json
import time

from hollow import replay, signing, topics
from hollow.mqtt import connect, make_client, now_iso


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    inv = sub.add_parser("investigate", help="send a machine to check an investigation's spot")
    inv.add_argument("investigation")
    inv.add_argument("--unit", required=True)
    inv.add_argument("--x", type=float, required=True)
    inv.add_argument("--y", type=float, required=True)
    inv.add_argument("--sandbox", action="store_true")
    ks = sub.add_parser("killswitch", help="engage or release the kill switch")
    ks.add_argument("state", choices=["on", "off"])
    args = parser.parse_args()

    key = signing.load_signing_key(signing.OPERATOR)
    stamper = replay.Stamper()
    if args.action == "investigate":
        topic, retain = topics.command(args.unit), False
        message = {
            "task_id": f"task-{args.investigation}", "unit_id": args.unit, "type": "investigate",
            "params": {"x": args.x, "y": args.y, "investigation": args.investigation},
            "status": "approved", "sandbox": args.sandbox, "requested_by": "cli", "approved_by": "operator",
        }
    else:
        topic, retain = topics.KILLSWITCH, True
        message = {"engaged": args.state == "on", "by": "operator"}
    message.update({"gateway_id": signing.OPERATOR, "timestamp": now_iso()})
    envelope = signing.seal(signing.OPERATOR, key, stamper.stamp(message))

    client = make_client("operator-cli")
    connect(client)
    client.loop_start()
    client.publish(topic, json.dumps(envelope), retain=retain).wait_for_publish()
    time.sleep(0.3)
    client.loop_stop()
    client.disconnect()
    print(f"Signed and sent: {args.action} {getattr(args, 'state', '') or args.investigation}")


if __name__ == "__main__":
    main()
