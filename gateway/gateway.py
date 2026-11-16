"""Edge gateway: discover, fingerprint, translate and sign every machine at one site.

    python -m gateway.gateway            # uses GATEWAY_ID from .env

Listens on surface/raw/<gateway_id>/<protocol>/+ (this site's "local network"),
publishes discoveries to uplink/discovered and signed readings to
uplink/readings/<zone>/<unit_id>.
"""

import argparse
import json
import logging
from pathlib import Path

import paho.mqtt.client as paho

from hollow import config, geo, signing, topics
from hollow.mqtt import connect, make_client, now_iso

from .adapters import ADAPTERS, Fingerprint

log = logging.getLogger("gateway")
HERE = Path(__file__).resolve().parent


def load_unit_map() -> dict[str, dict]:
    path = HERE / "config.json"
    if not path.exists():
        path = HERE / "config.example.json"
        log.warning("gateway/config.json not found, using config.example.json")
    return json.loads(path.read_text(encoding="utf-8")).get("units", {})


class Gateway:
    def __init__(self, gateway_id: str):
        self.gateway_id = gateway_id
        self.signing_key = signing.load_signing_key(gateway_id)
        self.unit_map = load_unit_map()
        self.known: dict[str, Fingerprint] = {}
        self.protocol_of: dict[str, str] = {}
        self.handled_tasks: set[str] = set()
        self.done_tasks: set[str] = set()
        self.killswitch = False
        self.client = make_client(f"gateway-{gateway_id}")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def run(self) -> None:
        connect(self.client)
        log.info("Gateway %s connecting to %s:%s", self.gateway_id, config.MQTT_HOST, config.MQTT_PORT)
        self.client.loop_forever()

    def on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            log.error("Connect failed: %s", reason_code)
            return
        for protocol in ADAPTERS:
            client.subscribe(topics.raw_wildcard(self.gateway_id, protocol))
        client.subscribe([(topics.COMMANDS_ALL, 0), (topics.KILLSWITCH, 0)])
        public = signing.b64(bytes(self.signing_key.verify_key))
        client.publish(
            topics.key(self.gateway_id),
            json.dumps({"gateway_id": self.gateway_id, "public_key": public}),
            retain=True,
        )
        log.info("Listening on %s", topics.raw_wildcard(self.gateway_id))

    # ---------- commands: the "down" half of each adapter ----------
    def ack(self, cmd: dict, result: str, reason: str | None = None) -> None:
        self.client.publish(
            topics.ack(cmd["unit_id"]),
            json.dumps({"task_id": cmd.get("task_id"), "unit_id": cmd["unit_id"], "result": result,
                        "reason": reason, "timestamp": now_iso()}),
        )
        log.info("Task %s for %s: %s%s", cmd.get("task_id"), cmd["unit_id"], result, f" ({reason})" if reason else "")

    def on_command(self, msg: paho.MQTTMessage) -> None:
        try:
            cmd = json.loads(msg.payload)
        except json.JSONDecodeError:
            return
        unit_id, task_id = cmd.get("unit_id"), cmd.get("task_id")
        # Only machines at this site, only human-approved tasks, each task once.
        if unit_id not in self.known or cmd.get("status") != "approved" or task_id in self.handled_tasks:
            return
        self.handled_tasks.add(task_id)
        if self.killswitch and cmd.get("type") != "stop":
            self.ack(cmd, "rejected", "kill switch engaged")
            return
        if cmd.get("sandbox"):
            self.ack(cmd, "done", "sandbox: not sent to the machine")
            return
        protocol = self.protocol_of[unit_id]
        raw_cmd = ADAPTERS[protocol].command(self.known[unit_id].kind, cmd)
        if raw_cmd is None:
            self.ack(cmd, "rejected", f"a {self.known[unit_id].kind} can't do {cmd.get('type')}")
            return
        self.client.publish(topics.machine_command(self.gateway_id, protocol, unit_id), json.dumps(raw_cmd))
        self.ack(cmd, "accepted")

    def on_killswitch(self, msg: paho.MQTTMessage) -> None:
        try:
            engaged = bool(json.loads(msg.payload).get("engaged"))
        except (json.JSONDecodeError, AttributeError):
            return
        if engaged and not self.killswitch:
            log.warning("KILL SWITCH ENGAGED: halting every machine that can move")
            for unit_id, fp in self.known.items():
                raw_cmd = ADAPTERS[self.protocol_of[unit_id]].command(fp.kind, {"type": "stop", "task_id": "killswitch"})
                if raw_cmd:
                    self.client.publish(topics.machine_command(self.gateway_id, self.protocol_of[unit_id], unit_id), json.dumps(raw_cmd))
        self.killswitch = engaged

    def on_message(self, client, userdata, msg: paho.MQTTMessage) -> None:
        if msg.topic.startswith("uplink/commands/"):
            self.on_command(msg)
            return
        if msg.topic == topics.KILLSWITCH:
            self.on_killswitch(msg)
            return
        # surface/raw/<gateway_id>/<protocol>/<unit_id>
        parts = msg.topic.split("/")
        if len(parts) != 5:
            return
        protocol, unit_id = parts[3], parts[4]
        adapter = ADAPTERS.get(protocol)
        if adapter is None:
            return
        try:
            raw = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.warning("%s: unparseable payload", unit_id)
            return

        if unit_id not in self.known:
            self.protocol_of[unit_id] = protocol
            self.discover(unit_id, protocol, msg.topic, adapter.fingerprint(raw))
        fp = self.known[unit_id]
        if fp.kind == "unknown":
            return

        readings = adapter.translate(fp.kind, raw)
        extras = adapter.extras(fp.kind, raw) if hasattr(adapter, "extras") else {}
        if not readings and not extras:
            return

        # Machines that move report their own position; fixed ones are placed by config.
        if "location" in extras:
            location = extras.pop("location")
            zone = geo.zone_for(location["x"], location["y"])
        else:
            placement = self.unit_map.get(unit_id, {})
            location = {"x": placement["x"], "y": placement["y"]} if "x" in placement else None
            zone = placement.get("zone", "UNMAPPED")
        reading = {
            "unit_id": unit_id,
            "gateway_id": self.gateway_id,
            "protocol": protocol,
            "kind": fp.kind,
            "zone": zone,
            "location": location,
            "timestamp": now_iso(),
            "readings": readings,
            "status": "online",
            **extras,
        }
        envelope = signing.seal(self.gateway_id, self.signing_key, reading)
        client.publish(topics.reading(zone, unit_id), json.dumps(envelope))

        task = extras.get("task")
        if task and task["state"] == "arrived" and task["task_id"] not in self.done_tasks:
            self.done_tasks.add(task["task_id"])
            self.ack({"task_id": task["task_id"], "unit_id": unit_id}, "done", "arrived at target")

    def discover(self, unit_id: str, protocol: str, address: str, fp: Fingerprint) -> None:
        self.known[unit_id] = fp
        log.info("Discovered %s (%s): %s, confidence %.1f", unit_id, protocol, fp.kind, fp.confidence)
        self.client.publish(
            topics.DISCOVERED,
            json.dumps({
                "gateway_id": self.gateway_id,
                "unit_id": unit_id,
                "protocol": protocol,
                "address": address,
                "fingerprint": {"kind": fp.kind, "confidence": fp.confidence, "evidence": fp.evidence},
                "timestamp": now_iso(),
            }),
        )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", default=config.GATEWAY_ID)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    try:
        Gateway(args.gateway).run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
