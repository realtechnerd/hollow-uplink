"""Edge gateway: discover, fingerprint, translate and sign every machine at one site.

    python -m gateway.gateway            # uses GATEWAY_ID from .env

Listens on surface/raw/<gateway_id>/<protocol>/+ (this site's "local network"),
publishes discoveries to uplink/discovered and signed readings to
uplink/readings/<zone>/<unit_id>.
"""

import argparse
import json
import logging
import threading
from pathlib import Path

import paho.mqtt.client as paho

from hollow import config, geo, replay, signing, topics
from hollow.mqtt import connect, make_client, now_iso

from .adapters import ADAPTERS, Fingerprint
from .adapters.modbus_adapter import ModbusScanner

log = logging.getLogger("gateway")
HERE = Path(__file__).resolve().parent


def load_site_config() -> dict:
    path = HERE / "config.json"
    if not path.exists():
        path = HERE / "config.example.json"
        log.warning("gateway/config.json not found, using config.example.json")
    return json.loads(path.read_text(encoding="utf-8"))


class Gateway:
    def __init__(self, gateway_id: str):
        self.gateway_id = gateway_id
        self.signing_key = signing.load_signing_key(gateway_id)
        site = load_site_config()
        self.unit_map = site.get("units", {})
        self.modbus_config = site.get("modbus")
        self.lock = threading.Lock()           # MQTT callbacks and the Modbus poller both feed readings in
        self.known: dict[str, Fingerprint] = {}
        self.protocol_of: dict[str, str] = {}
        self.handled_tasks: set[str] = set()
        self.done_tasks: set[str] = set()
        self.killswitch = False
        self.stamper = replay.Stamper()        # boot + seq on everything we sign
        self.trust = signing.TrustStore()      # who may sign commands (the operator)
        self.guard = replay.ReplayGuard()
        self.client = make_client(f"gateway-{gateway_id}")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    def run(self) -> None:
        connect(self.client)
        log.info("Gateway %s connecting to %s:%s", self.gateway_id, config.MQTT_HOST, config.MQTT_PORT)
        if self.modbus_config:
            ModbusScanner(
                self.modbus_config.get("scan", []),
                on_raw=lambda unit_id, address, raw: self.handle_raw("modbus", unit_id, address, raw),
                poll_s=float(self.modbus_config.get("poll_s", 2)),
                rescan_s=float(self.modbus_config.get("rescan_s", 30)),
            ).start()
        self.client.loop_forever()

    def on_connect(self, client, userdata, flags, reason_code, properties) -> None:
        if reason_code.is_failure:
            log.error("Connect failed: %s", reason_code)
            return
        # Only MQTT-native machines arrive over MQTT; Modbus devices are polled directly.
        client.subscribe(topics.raw_wildcard(self.gateway_id, "mqtt"))
        client.subscribe([(topics.COMMANDS_ALL, 0), (topics.KILLSWITCH, 0)])
        public = signing.b64(bytes(self.signing_key.verify_key))
        client.publish(
            topics.key(self.gateway_id),
            json.dumps({"gateway_id": self.gateway_id, "public_key": public}),
            retain=True,
        )
        log.info("Listening on %s", topics.raw_wildcard(self.gateway_id, "mqtt"))

    # ---------- commands: the "down" half of each adapter ----------
    def ack(self, cmd: dict, result: str, reason: str | None = None) -> None:
        ack = self.stamper.stamp({
            "gateway_id": self.gateway_id, "task_id": cmd.get("task_id"), "unit_id": cmd["unit_id"],
            "result": result, "reason": reason, "timestamp": now_iso(),
        })
        self.client.publish(topics.ack(cmd["unit_id"]), json.dumps(signing.seal(self.gateway_id, self.signing_key, ack)))
        log.info("Task %s for %s: %s%s", cmd.get("task_id"), cmd["unit_id"], result, f" ({reason})" if reason else "")

    def open_operator_message(self, msg: paho.MQTTMessage) -> dict | None:
        """Commands and the kill switch must be signed by the operator key and not replayed."""
        try:
            message = self.trust.open(json.loads(msg.payload), role=signing.OPERATOR)
            if not self.guard.check(msg.topic, message, retained=msg.retain):
                return None  # the broker re-sent a retained message we already handled
            return message
        except (ValueError, json.JSONDecodeError, KeyError, TypeError) as exc:
            log.warning("Rejected message on %s: %s", msg.topic, exc)
            return None

    def on_command(self, msg: paho.MQTTMessage) -> None:
        cmd = self.open_operator_message(msg)
        if cmd is None:
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
        state = self.open_operator_message(msg)
        if state is None:
            return
        engaged = bool(state.get("engaged"))
        if engaged and not self.killswitch:
            log.warning("KILL SWITCH ENGAGED: halting every machine that can move")
            for unit_id, fp in self.known.items():
                raw_cmd = ADAPTERS[self.protocol_of[unit_id]].command(fp.kind, {"type": "stop", "task_id": "killswitch"})
                if raw_cmd:
                    self.client.publish(topics.machine_command(self.gateway_id, self.protocol_of[unit_id], unit_id), json.dumps(raw_cmd))
        self.killswitch = engaged

    def on_message(self, client, userdata, msg: paho.MQTTMessage) -> None:
        with self.lock:
            if msg.topic.startswith("uplink/commands/"):
                self.on_command(msg)
                return
            if msg.topic == topics.KILLSWITCH:
                self.on_killswitch(msg)
                return
        # surface/raw/<gateway_id>/mqtt/<unit_id>
        parts = msg.topic.split("/")
        if len(parts) != 5 or parts[3] != "mqtt":
            return
        unit_id = parts[4]
        try:
            raw = json.loads(msg.payload)
        except (json.JSONDecodeError, UnicodeDecodeError):
            log.warning("%s: unparseable payload", unit_id)
            return
        self.handle_raw("mqtt", unit_id, msg.topic, raw)

    def handle_raw(self, protocol: str, unit_id: str, address: str, raw) -> None:
        """Any protocol, after its transport: fingerprint once, then translate, sign, publish."""
        with self.lock:
            self._handle_raw(protocol, unit_id, address, raw)

    def _handle_raw(self, protocol: str, unit_id: str, address: str, raw) -> None:
        adapter = ADAPTERS[protocol]
        client = self.client
        if unit_id not in self.known:
            self.protocol_of[unit_id] = protocol
            self.discover(unit_id, protocol, address, adapter.fingerprint(raw))
        elif self.protocol_of[unit_id] != protocol:
            log.warning("%s seen on %s but was discovered on %s; ignoring", unit_id, protocol, self.protocol_of[unit_id])
            return
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
        self.stamper.stamp(reading)
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
