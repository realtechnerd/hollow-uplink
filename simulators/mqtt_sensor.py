"""Fake MQTT-native surface machines.

Each kind of machine speaks its own raw format on purpose, so the gateway has
to fingerprint and translate them. Values come from the hidden world's true
environment at the unit's position. Publishes to surface/raw/<gateway_id>/mqtt/<unit_id>.

    python -m simulators.mqtt_sensor                 # 3 healthy units for GATEWAY_ID
    python -m simulators.mqtt_sensor --faulty mqtt-rad-02
"""

import argparse
import json
import random
import time
from pathlib import Path

from hollow import config, topics
from hollow.mqtt import connect, make_client

from .world import environment

GATEWAY_DIR = Path(__file__).resolve().parent.parent / "gateway"


def air_sensor(env: dict, rng: random.Random) -> dict:
    # Vendor A: terse keys, temp in C
    return {
        "pm25": round(env["aqi"] * 0.28 + rng.uniform(-1, 1), 1),
        "co2_ppm": int(430 + rng.uniform(-15, 15)),
        "t": env["temp_c"],
        "aqi": env["aqi"],
    }


def radiation_probe(env: dict, rng: random.Random) -> dict:
    # Vendor B: Geiger counter style
    dose = env["radiation_usv_h"]
    return {"cpm": int(dose / 0.0065), "dose_rate": dose, "unit": "uSv/h"}


def weather_mast(env: dict, rng: random.Random) -> dict:
    # Vendor C: imperial units, nested
    return {
        "station": {"model": "WX-9", "fw": "4.2.1"},
        "obs": {
            "temperature_f": round(env["temp_c"] * 9 / 5 + 32, 1),
            "humidity": int(55 + rng.uniform(-4, 4)),
            "wind_kph": round(12 + rng.uniform(-3, 5), 1),
        },
    }


KINDS = {"air": air_sensor, "rad": radiation_probe, "wx": weather_mast}


def compromised(raw: dict, value: float, rng: random.Random) -> dict:
    """A hijacked sensor telling a believable lie: plausible values, natural jitter,
    internally consistent, validly signed by the gateway. Only independent measurement catches it."""
    v = value * rng.uniform(0.95, 1.05)
    if "dose_rate" in raw:
        return {**raw, "dose_rate": round(v, 3), "cpm": int(v / 0.0065)}
    if "aqi" in raw:
        return {**raw, "aqi": int(v), "pm25": round(v * 0.28, 1)}
    return raw


def faulty(raw: dict) -> dict:
    """A broken sensor: values stuck far out of range."""
    if "dose_rate" in raw:
        return {**raw, "cpm": 9999, "dose_rate": 64.99}
    if "aqi" in raw:
        return {**raw, "aqi": 500, "pm25": 999.0}
    if "obs" in raw:
        return {**raw, "obs": {**raw["obs"], "temperature_f": 212.0}}
    return raw


def unit_positions() -> dict[str, dict]:
    """The simulator knows where its machines physically are (the gateway learns it from config)."""
    for name in ("config.json", "config.example.json"):
        path = GATEWAY_DIR / name
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8")).get("units", {})
    return {}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", default=config.GATEWAY_ID)
    parser.add_argument(
        "--units",
        default="mqtt-air-01,mqtt-rad-02,mqtt-wx-03,mqtt-rad-04",
        help="comma-separated unit ids; the middle part (air/rad/wx) picks the kind",
    )
    parser.add_argument("--faulty", default="", help="comma-separated unit ids that report garbage")
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between messages")
    args = parser.parse_args()

    units = [u.strip() for u in args.units.split(",") if u.strip()]
    faulty_units = {u.strip() for u in args.faulty.split(",") if u.strip()}
    for unit in units:
        if unit.split("-")[1] not in KINDS:
            parser.error(f"{unit}: kind must be one of {', '.join(KINDS)}")
    positions = unit_positions()
    rng = random.Random()
    lies: dict[str, float] = {}  # unit -> value a compromised unit reports

    def on_control(client, userdata, msg):
        # Simulation-only: lets the demo compromise or restore a sensor while running.
        try:
            ctl = json.loads(msg.payload)
        except json.JSONDecodeError:
            return
        unit = ctl.get("unit_id")
        if ctl.get("mode") == "compromised":
            lies[unit] = float(ctl["value"])
            print(f"{unit} is now compromised: reporting {lies[unit]}")
        elif ctl.get("mode") == "healthy":
            lies.pop(unit, None)
            faulty_units.discard(unit)
            print(f"{unit} restored")

    client = make_client(f"sim-{args.gateway}")
    client.on_connect = lambda c, u, f, rc, p: c.subscribe(topics.sim_control(args.gateway))
    client.on_message = on_control
    connect(client)
    client.loop_start()
    print(f"Simulating {', '.join(units)} for {args.gateway} on {config.MQTT_HOST}:{config.MQTT_PORT}")

    start = time.time()
    try:
        while True:
            t = time.time() - start
            for unit in units:
                pos = positions.get(unit, {"x": 400, "y": 250})
                env = environment(pos["x"], pos["y"], t, rng)
                raw = KINDS[unit.split("-")[1]](env, rng)
                if unit in faulty_units:
                    raw = faulty(raw)
                elif unit in lies:
                    raw = compromised(raw, lies[unit], rng)
                client.publish(topics.raw(args.gateway, "mqtt", unit), json.dumps(raw))
            time.sleep(args.interval)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
