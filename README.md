# Hollow Uplink

*The machines kept us out. Now they're building our way home.*

A universal translator for the unknown machines still running the surface. Edge gateways discover machines, work out how each one talks, translate everything into one common format, and sign every reading so the Directorate can't fake or bury the answer.

Patriot HackFest Fall 2026, Life Underground track (problems #13, #14, #15, and #7).

**World assumption:** the machines were built by humans of our era and later, so they speak real protocols (MQTT, Modbus, ROS). The protocols, discovery, translation, signing and dashboard are real code; only the machines are simulated.

The data contract (message formats and topics) lives in the project outline doc. Change it only by team agreement.

## How it fits together

```
MQTT machines ───publish──▶ edge gateway ──signed readings──▶ broker ──▶ verifier, dashboards
Modbus devices ◀──scan+poll──  discover, fingerprint,                     (check every signature)
 (per site)                    translate, sign
```

**Protocols.** Two real protocols today, each with a different discovery problem:

| Protocol | How the gateway finds a machine | How it learns what it is | Translation work |
| --- | --- | --- | --- |
| MQTT | Machine publishes on the site's raw topics | Shape of its JSON (which keys it has) | Field names, nesting, °F to °C, units |
| Modbus TCP | Gateway scans the site's local addresses (private and loopback only, at most 256) | Read Device Identification (function 43/14): vendor and product code | Raw 16-bit registers: scaling, signed values, 32-bit values across two registers, a vendor that stores them word-swapped |

ROS 2 (via rosbridge) is planned; adding it is one new adapter in `gateway/adapters/`.

Each teammate runs one edge gateway for their "site". It reads only its own site's raw topics (`surface/raw/<gateway_id>/...`), so the demo really runs across several states over one cloud broker.

| Path | What it is |
| --- | --- |
| `hollow/` | Shared code: config, MQTT client, topic names, Ed25519 signed envelopes |
| `gateway/` | Edge gateway and protocol adapters (`gateway/adapters/`; a new protocol = one new adapter). `modbus_adapter.py` holds the register maps and the scanner |
| `simulators/` | Fake MQTT machines in their own vendor formats (`--faulty` units), real Modbus TCP servers acting as two vendors' field devices (`modbus_device.py`), scout robots that map the surface with simulated LiDAR, and `world.py`, the hidden "true" city only simulators can see |
| `dashboard/city.html` | 3D surface view (Three.js). Starts dark; every tile comes from a verified scout survey and fades as its scan ages |
| `dashboard/index.html` | Operator view: flat map, machine table, event feed |
| `dashboard/uplink.js` | Shared live-data client: MQTT plus signature checks, used by both views |
| `verifier/` | Flags faulty sensors, scores each zone (outposts / domes / suits / not safe / unknown) with a confidence and a plain-language verdict, and publishes a signed map snapshot for late viewers. Thresholds in `verifier/scoring.py` |
| `tools/keygen.py` | Makes a gateway key and adds its public key to `keys/trusted.json` |
| `tools/spoof.py` | Sends forged or tampered readings to show they get rejected |
| `tools/operator.py` | Operator actions from the command line (approve a task, kill switch), signed with the operator key |
| `tools/attack.py` | Simulation only: makes a running sensor tell a believable, validly signed lie |
| `dev/broker.py` | Local MQTT broker with no Docker (`docker compose up` works too) |

## Run it locally (Windows PowerShell; on macOS/Linux use `.venv/bin/python`)

One-time setup:

```
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m tools.keygen gw-local
.\.venv\Scripts\python -m tools.keygen verifier
.\.venv\Scripts\python -m tools.keygen operator
```

The operator key approves tasks and controls the kill switch. Keep `keys/operator.key` private; it is git-ignored and the dev server refuses to serve `.key` files.

Then in seven terminals:

```
.\.venv\Scripts\python dev\broker.py
.\.venv\Scripts\python -m simulators.modbus_device
.\.venv\Scripts\python -m gateway.gateway --gateway gw-local
.\.venv\Scripts\python -m verifier.verifier
.\.venv\Scripts\python -m simulators.mqtt_sensor --gateway gw-local --faulty mqtt-rad-02
.\.venv\Scripts\python -m simulators.scout --gateway gw-local --scouts 3 --tick 0.3
.\.venv\Scripts\python dev\serve.py
```

The Modbus devices listen on 127.0.0.1:5020-5022, each one standing in for a separate box on the site network. The gateway scans the addresses in `gateway/config.json` (`modbus.scan`), asks each device who it is, and re-scans every 30 s, so a device started later still gets found.

`dev/serve.py` is a static server with caching turned off, so a normal reload picks up dashboard edits.

Open http://localhost:8000/dashboard/city.html. The **Surface report** on the left is the resident's answer: the Directorate's claim, then a plain-language verdict per zone from the verifier. Within seconds the verifier flags `mqtt-rad-02` as faulty (pinned at its maximum, stuck, and contradicted by nearby scouts) and leaves it out. The surface starts dark; the scouts reveal it as they explore (about 40% mapped after a minute, 95% after five). Hover a tile to see when and by whom it was scanned, and click a machine for its readings and signature status. The operator view is at http://localhost:8000/dashboard/.

**Why the map is honest:** the gateway and dashboard never read `simulators/world.py`. They only know what scouts report, in signed readings, so a forged survey is rejected like any forged reading.

Try the attacks:

```
.\.venv\Scripts\python -m tools.spoof forged mqtt-air-01 --zone Z1 --gateway gw-local
.\.venv\Scripts\python -m tools.spoof tampered mqtt-wx-03 --zone Z2 --gateway gw-local
```

Both show as rejected in the event feed: signatures catch altered or forged messages. A third attack records a genuine signed reading and re-sends the exact bytes:

```
.\.venv\Scripts\python -m tools.spoof replayed scout-01
```

The signature is valid, but the sequence number has already been seen, so it is rejected as replayed.

**Operator mode.** The 3D view opens read-only. Click **Unlock operator** and pick `keys/operator.key` to approve investigations, use **Sandbox** (tasks are acknowledged but never sent to machines) and the **Kill switch** (every robot halts and new tasks are refused, for everyone, until released). The same actions are scriptable:

```
.\.venv\Scripts\python -m tools.operator killswitch on
.\.venv\Scripts\python -m tools.operator killswitch off
```

**The lie signatures can't catch.** Once Z2 has a verdict (a couple of minutes of scouting), hijack its radiation probe:

```
.\.venv\Scripts\python -m tools.attack compromise mqtt-rad-04 --value 8
```

The probe now reports a believable 8 µSv/h, passed through the real gateway and validly signed. Z2's verdict drops. The verifier notices that one unconfirmed machine is changing the answer and opens an investigation. In the 3D view's Surface report, click **Approve: send scout-0X to check**. The scout drives there (dashed line), measures about 0.13 µSv/h, the verifier marks the claim **refuted**, flags the probe as compromised and Z2's verdict recovers. If the reading had been real, the investigation would say **confirmed** instead. Undo with `tools.attack restore mqtt-rad-04` (the verifier keeps treating a refuted machine as compromised until it restarts).

Any independent machine measuring the spot settles an investigation, so a scout that happens to drive past can resolve it before the one you sent arrives. For a clean recording, run a single scout (`--scouts 1`) or attack a sensor the scouts are far from. The verifier also opens investigations on its own for real hazards only one machine has seen (for example the smoke in Z5); those end **confirmed**.

## Joining the shared cloud broker (each teammate)

1. Copy `.env.example` to `.env` and fill in the HiveMQ Cloud host and credentials (`MQTT_PORT=8883`, `MQTT_TLS=1`) and your own `GATEWAY_ID`, e.g. `gw-va`.
2. `python -m tools.keygen gw-va`, then commit `keys/trusted.json`. Never commit `keys/*.key`.
3. Copy `gateway/config.example.json` to `gateway/config.json` and place your units on the map.
4. Run your gateway and simulator. Use unique unit ids across the team (e.g. `mqtt-air-11`).

The dashboard's `config.js` is public once hosted, so give it its own broker user, not the gateways' credentials.

## What's not built yet

- ROS 2 adapter (planned, via rosbridge; needs Docker for the ROS side)
- Modbus devices are read-only sensors; writing registers (e.g. switching a vent) isn't supported yet
- Zone goals ("make Z4 outpost-ready") with a planner that turns them into tasks for approval
- Readings are signed at the gateway, not by the device, so a compromised gateway could still lie; investigations are the defence there
- The local dev broker (amqtt) also delivers unrelated retained messages on subscribe; use Mosquitto or HiveMQ for anything real
- Structural sensors (tilt and vibration) to flag unstable buildings
- Discovery events aren't retained, so a dashboard opened later learns about machines from their readings instead
- The verifier keeps its map in memory; restarting it starts the map over
