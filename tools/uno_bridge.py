"""Bridge a real Arduino Uno (hardware/uno_station) onto the site's local MQTT network.

The Uno has no network port, so this script is its network card and nothing more: it forwards
each JSON line the board prints to surface/raw/<gateway_id>/mqtt/<unit_id>, untouched, and
forwards the gateway's commands for that unit back down the USB cable. The gateway still has to
fingerprint, translate and sign the board's readings itself.

    python -m tools.uno_bridge --port COM3                # Windows (Device Manager shows the port)
    python -m tools.uno_bridge --port /dev/ttyACM0        # Linux / macOS (/dev/cu.usbmodem*)
    python -m tools.uno_bridge --fake                     # no board: imitate one, with a smoke event every 90 s
"""

import argparse
import json
import random
import threading
import time

from hollow import config, topics
from hollow.mqtt import connect, make_client

BAUD = 9600


class FakeUno:
    """Speaks the same serial format as uno_station.ino, for testing without the board."""

    def __init__(self, warmup_s: float = 6.0):
        self.start = time.time()
        self.warmup_s = warmup_s
        self.relay = False
        self.base = 0
        self.rng = random.Random()
        self.next_at = self.start

    def write(self, data: bytes) -> None:
        cmd = data.decode().strip()
        if cmd in ("R0", "R1"):
            self.relay = cmd == "R1"

    def readline(self) -> bytes:
        time.sleep(max(0.0, self.next_at - time.time()))
        self.next_at += 2
        up = time.time() - self.start
        if not self.base and up >= self.warmup_s:
            self.base = 180
            return b"# gas baseline 180\n"
        cycle = (up - self.warmup_s) % 90
        smoke = self.base and 40 <= cycle < 60          # someone blows smoke into the box
        adc = int(180 * (4.5 if smoke else 1.0) + self.rng.uniform(-6, 6))
        line = {
            "dev": "BX-1", "fw": "1.0", "up_s": int(up),
            "gas": {"adc": adc, "base": self.base, "warm": 0 if self.base else 1},
            "dht": {"t": round(22 + self.rng.choice((0, 0, 1)), 1), "rh": 45},
            "ds": {"t": round(22.4 + self.rng.uniform(-0.1, 0.1), 2)},
            "light": int(600 + self.rng.uniform(-10, 10)), "hatch": int(smoke), "relay": int(self.relay),
        }
        return (json.dumps(line) + "\n").encode()


def open_serial(port: str):
    import serial  # pyserial; only needed with a real board

    ser = serial.Serial(port, BAUD, timeout=5)
    time.sleep(2)  # opening the port resets the Uno; give the bootloader time to hand over
    return ser


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", help="serial port of the Uno, e.g. COM3")
    parser.add_argument("--fake", action="store_true", help="imitate a board instead of opening a port")
    parser.add_argument("--unit", default="uno-gas-01", help="unit id; place it on the map in gateway/config.json")
    parser.add_argument("--gateway", default=config.GATEWAY_ID)
    args = parser.parse_args()
    if not args.port and not args.fake:
        parser.error("give --port (e.g. COM3) or --fake")

    board = FakeUno() if args.fake else open_serial(args.port)
    write_lock = threading.Lock()
    raw_topic = topics.raw(args.gateway, "mqtt", args.unit)
    cmd_topic = topics.machine_command(args.gateway, "mqtt", args.unit)

    def on_command(client, userdata, msg):
        # The gateway only sends this after checking the operator's signature; we just relay it.
        try:
            cmd = json.loads(msg.payload)
        except json.JSONDecodeError:
            return
        if cmd.get("op") == "relay":
            line = "R1" if cmd.get("on") else "R0"
            with write_lock:
                board.write((line + "\n").encode())
            print(f"-> board: {line} ({cmd.get('ref')})")

    client = make_client(f"uno-bridge-{args.unit}")
    client.on_connect = lambda c, u, f, rc, p: c.subscribe(cmd_topic)
    client.on_message = on_command
    connect(client)
    client.loop_start()
    print(f"Bridging {'fake board' if args.fake else args.port} as {args.unit} -> {raw_topic}")

    try:
        while True:
            line = board.readline().decode(errors="replace").strip()
            if not line:
                continue
            if line.startswith("#"):
                print(f"board {line}")      # the sketch's own log lines
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                print(f"skipped garbled line: {line[:60]!r}")   # usually half a line right after reset
                continue
            if raw.get("gas", {}).get("warm"):
                print(f"board warming up ({raw.get('up_s')} s)")
            client.publish(raw_topic, json.dumps(raw))
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
