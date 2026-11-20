"""The scripted demo: runs the story beat by beat on a running stack, with on-screen captions.

    python run.py --demo            # easiest: starts everything, then this
    python -m tools.demo            # on a stack that's already running; press Enter for each step
    python -m tools.demo --auto     # advances by itself (for recordings)

If the Arduino field station (hardware/README.md) is reporting, or its stand-in from
uno_bridge --fake, the demo includes a step for it: smoke in the box, a forged command that
goes nowhere, and the operator switching its relay on. Without it, that step is skipped.

Each step says what to point at, waits for Enter (or a few seconds with --auto), triggers the
event, then waits until the system has actually reacted before moving on. Captions go to the
3D view when it's opened with ?captions=1 (run.py --demo does this).

What's staged and what's real: the attacks are the same tools as in the README (spoof.py,
attack.py). The one piece of staging is simulation-only: while the hijacked probe is being
investigated, untasked scouts keep out of a small circle around it, so the scout the operator
sends is the one that settles it rather than one that happens to drive past. Everything the
dashboards show still comes through the real gateway, verifier and signatures.
"""

import argparse
import json
import math
import queue
import subprocess
import sys
import threading
import time

from hollow import config, replay, signing, topics
from hollow.mqtt import connect, make_client, now_iso

CAPTION = "demo/caption"
SUSPECT = "mqtt-rad-04"           # the probe the Directorate hijacks
SUSPECT_AT = (330, 80)            # where it is (gateway/config.example.json)
SUSPECT_ZONE = "Z2"
LIE = 8.0                         # uSv/h: alarming but believable
KEEP_AWAY_R = 70                  # map units; investigations count readings within 40
FAULTY = "mqtt-rad-02"
REC_LABEL = {"outposts": "Outposts", "domes": "Domes", "suits": "Suits only", "not_safe": "Unsafe",
             "unknown": "Unknown"}     # same words as the 3D view's zone labels


def scoring_label(rec: str) -> str:
    return REC_LABEL.get(rec, rec.replace("_", " "))
STATION_ZONE = "Z5"               # where uno-gas-01 sits (gateway/config.example.json)


class State:
    """What the director has seen on the broker, from verified messages only."""

    def __init__(self):
        self.lock = threading.Lock()
        self.scores: dict[str, dict] = {}
        self.investigations: list[dict] = []
        self.scouts: dict[str, tuple[float, float]] = {}
        self.station: tuple[str, dict] | None = None   # (unit_id, latest readings) of a field station
        self.readings = 0

    def investigation_for(self, suspect: str) -> dict | None:
        with self.lock:
            found = [i for i in self.investigations if i.get("suspect") == suspect]
        return found[-1] if found else None


class Director:
    def __init__(self, gateway: str, auto: bool, start: int = 1):
        self.gateway, self.auto, self.start = gateway, auto, start
        self.state = State()
        self.trust = signing.TrustStore()
        self.enter: queue.Queue = queue.Queue()
        self.client = make_client("demo-director")
        self.client.on_connect = lambda c, u, f, rc, p: c.subscribe(
            [("uplink/zones/+/score", 0), (topics.INVESTIGATIONS, 0), ("uplink/readings/+/+", 0)]
        )
        self.client.on_message = self.on_message
        self.operator_key = None
        self.stamper = replay.Stamper()
        self.steps: list = []
        if not auto:
            threading.Thread(target=self._read_stdin, daemon=True).start()

    # ---------- input ----------
    def _read_stdin(self) -> None:
        for line in sys.stdin:
            self.enter.put(line)

    def on_message(self, client, userdata, msg) -> None:
        try:
            envelope = json.loads(msg.payload)
            parts = msg.topic.split("/")
            if parts[1] == "readings":
                reading = self.trust.open(envelope, role="gateway")
                with self.state.lock:
                    self.state.readings += 1
                    loc = reading.get("location")
                    if reading.get("kind") == "scout_robot" and loc:
                        self.state.scouts[reading["unit_id"]] = (loc["x"], loc["y"])
                    elif reading.get("kind") == "field_station":
                        self.state.station = (reading["unit_id"], reading.get("readings", {}))
            elif parts[1] == "zones":
                score = self.trust.open(envelope, role="verifier")
                with self.state.lock:
                    self.state.scores[score["zone"]] = score
            elif msg.topic == topics.INVESTIGATIONS:
                inv = self.trust.open(envelope, role="verifier")
                with self.state.lock:
                    self.state.investigations = inv.get("investigations", [])
        except (ValueError, KeyError, TypeError, IndexError, json.JSONDecodeError):
            pass  # unsigned or forged (including our own spoofs): not evidence

    # ---------- output ----------
    def caption(self, step: int, title: str, text: str, layer: str | None = None) -> None:
        message = {"step": step, "total": len(self.steps), "title": title, "text": text, "at": now_iso()}
        if layer:
            message["layer"] = layer
        self.client.publish(CAPTION, json.dumps(message), retain=True)

    def clear_caption(self) -> None:
        self.client.publish(CAPTION, "", retain=True).wait_for_publish(2)

    def sim(self, message: dict) -> None:
        self.client.publish(topics.sim_control(self.gateway), json.dumps(message)).wait_for_publish(2)

    def tool(self, *args: str) -> None:
        """Run one of the README's tools (spoof, attack) exactly as a person would."""
        subprocess.run([sys.executable, "-m", *args, "--gateway", self.gateway],
                       cwd=config.REPO_ROOT, check=False, stdout=subprocess.DEVNULL)

    def operator(self, topic: str, message: dict, retain: bool = False) -> None:
        if self.operator_key is None:
            self.operator_key = signing.load_signing_key(signing.OPERATOR)
        message.update({"gateway_id": signing.OPERATOR, "timestamp": now_iso()})
        envelope = signing.seal(signing.OPERATOR, self.operator_key, self.stamper.stamp(message))
        self.client.publish(topic, json.dumps(envelope), retain=retain).wait_for_publish(2)

    # ---------- pacing ----------
    def pause(self, prompt: str, auto_s: float) -> None:
        if self.auto:
            time.sleep(auto_s)
            return
        while not self.enter.empty():
            self.enter.get_nowait()
        print(f"   \033[2m[Enter] {prompt}\033[0m", flush=True)
        self.enter.get()

    def wait_until(self, check, what: str, timeout: float, enter_skips: bool = True):
        """Poll check() until it returns something truthy. Enter (when not --auto) skips ahead."""
        deadline = time.time() + timeout
        shown = False
        while time.time() < deadline:
            result = check()
            if result:
                return result
            if not shown:
                print(f"   … waiting for {what}" + (" (Enter skips)" if enter_skips and not self.auto else ""), flush=True)
                shown = True
            if enter_skips and not self.auto:
                try:
                    self.enter.get(timeout=1)
                    return None
                except queue.Empty:
                    continue
            time.sleep(1)
        print(f"   ! gave up waiting for {what} after {timeout:.0f} s; carrying on", flush=True)
        return None

    def say(self, step: int, title: str, text: str, layer: str | None = None) -> None:
        print(f"\n\033[1m{step}/{len(self.steps)}  {title}\033[0m\n   {text}", flush=True)
        self.caption(step, title, text, layer)

    # ---------- the story ----------
    def run(self) -> None:
        connect(self.client)
        self.client.loop_start()
        print("\033[1mHollow Uplink: scripted demo\033[0m  (Ctrl+C to stop)", flush=True)
        if not self.wait_until(lambda: self.state.readings > 5, "readings from the gateway", 60, enter_skips=False):
            print("No signed readings are arriving. Is the stack running (python run.py)?")
        time.sleep(3)   # every machine has reported at least once
        hardware = [self.step_hardware] if self.state.station else []
        print(f"   Field station: {self.state.station[0] if self.state.station else 'not reporting, so its step is skipped'}", flush=True)
        self.steps = [
            self.step_dark, self.step_faulty, self.step_forged, self.step_replayed, self.step_layers,
            self.step_lie, self.step_investigate, self.step_verdict, *hardware, self.step_killswitch, self.step_end,
        ]
        try:
            for i, step in enumerate(self.steps, 1):
                if i >= self.start:
                    step(i)
        finally:
            self.sim({"scouts": "clear"})

    def step_dark(self, n: int) -> None:
        self.say(n, "Nobody has seen the surface in thirty years",
                 "Scout robots climb out of the old maintenance shaft. The map starts dark: every tile you see "
                 "comes from a scout's LiDAR scan, signed by the gateway, and fades as the scan gets old.")
        self.pause("next: a broken sensor", 25)

    def step_faulty(self, n: int) -> None:
        self.say(n, "A broken sensor is left out",
                 f"{FAULTY} has been pinned at its maximum since it switched on. The verifier flags it and leaves "
                 "it out of every verdict, instead of declaring the zone deadly.")
        self.wait_until(lambda: any(f.get("unit_id") == FAULTY for s in self.state.scores.values() for f in s.get("flagged", [])),
                        f"the verifier to flag {FAULTY}", 60)
        self.pause("next: forged and tampered messages", 12)

    def step_forged(self, n: int) -> None:
        self.say(n, "Forged and tampered messages are rejected",
                 "Someone sends a reading signed with a key nobody trusts, then a real reading edited in transit to "
                 "say the radiation is lethal. Watch the event feed: both are rejected, and nothing on the map changes.")
        self.pause("send them", 6)
        self.tool("tools.spoof", "forged", "mqtt-air-01", "--zone", "Z1")
        time.sleep(1.5)
        self.tool("tools.spoof", "tampered", "mqtt-wx-03", "--zone", "Z2")
        self.pause("next: a replayed message", 10)

    def step_replayed(self, n: int) -> None:
        self.say(n, "A replayed message is rejected too",
                 "This time a genuine, validly signed reading is recorded off the network and sent again. The "
                 "signature checks out, but its sequence number has already been seen.")
        self.pause("replay one", 5)
        self.tool("tools.spoof", "replayed", "scout-01")
        self.pause("next: map layers", 10)

    def step_layers(self, n: int) -> None:
        self.say(n, "One kind of data at a time",
                 "The strip on the right shows a single measurement across the city. Radiation: the old hospital "
                 "in Z3 glows. Every cell is the latest value a trusted machine measured there.", layer="radiation")
        self.pause("show CO₂", 12)
        self.caption(n, "One kind of data at a time",
                     "CO₂: the scouts have found gas pooling around a collapsed metro vent in Z4, and smoke from the "
                     "landfill fire in Z5. Grey cells are places nobody has measured yet.", layer="co2")
        print("   (showing CO₂)", flush=True)
        self.pause("next: the lie signatures can't catch", 12)
        self.caption(n, "One kind of data at a time", "Back to everything.", layer="all")

    def step_lie(self, n: int) -> None:
        self.sim({"scouts": "keep_away", "x": SUSPECT_AT[0], "y": SUSPECT_AT[1], "r": KEEP_AWAY_R})
        self.say(n, "The lie signatures can't catch",
                 f"The Directorate hijacks radiation probe {SUSPECT} in {SUSPECT_ZONE}. It reports a believable "
                 f"{LIE:g} µSv/h with natural jitter, through the real gateway, validly signed.")

        def ready():
            with self.state.lock:
                rec = (self.state.scores.get(SUSPECT_ZONE) or {}).get("recommendation")
                near = [u for u, p in self.state.scouts.items() if math.dist(p, SUSPECT_AT) < 50]
            return rec and rec != "unknown" and not near

        self.wait_until(ready, f"{SUSPECT_ZONE} to have a verdict, with no scout right beside the probe", 240)
        self.pause("hijack the probe", 6)
        self.tool("tools.attack", "compromise", SUSPECT, "--value", str(LIE))

    def step_investigate(self, n: int) -> None:
        self.say(n, "One machine is changing the answer",
                 f"Only {SUSPECT} reports the danger, and it would flip {SUSPECT_ZONE}'s verdict. The verifier "
                 "doesn't believe it or bury it: it opens an investigation and asks to send another machine to check.")
        inv = self.wait_until(lambda: (i := self.state.investigation_for(SUSPECT)) and i["status"] in ("open", "dispatched") and i,
                              "the verifier to open an investigation", 120)
        if not inv:
            return
        if inv["status"] == "open":
            print("   Click \"Approve: send scout-0X to check\" in the 3D view's Surface report (needs operator mode),")
            self.caption(n, "A human approves the trip",
                         "Machines never move on their own say-so. The operator approves the investigation, signing "
                         "the order with the operator key; the gateway only obeys operator-signed tasks.")
            if self.auto:
                time.sleep(8)
                self.approve(inv)
            else:
                print("   or press Enter to approve it from here.", flush=True)
                done = self.wait_until(lambda: (i := self.state.investigation_for(SUSPECT)) and i["status"] != "open",
                                       "an approval", 300)
                if not done:
                    self.approve(inv)

    def approve(self, inv: dict) -> None:
        with self.state.lock:
            scouts = dict(self.state.scouts)
        target = (inv["target"]["x"], inv["target"]["y"])
        unit = min(scouts, key=lambda u: math.dist(scouts[u], target)) if scouts else "scout-01"
        self.operator(topics.command(unit), {
            "task_id": f"task-{inv['id']}", "unit_id": unit, "type": "investigate",
            "params": {"x": target[0], "y": target[1], "investigation": inv["id"]},
            "status": "approved", "sandbox": False, "requested_by": "demo", "approved_by": "operator",
        })
        print(f"   Approved: {unit} sent to check {inv['id']}", flush=True)

    def step_verdict(self, n: int) -> None:
        self.say(n, "An independent machine settles it",
                 "The scout drives to the probe (dashed line) and measures the radiation itself.")
        inv = self.wait_until(lambda: (i := self.state.investigation_for(SUSPECT)) and i["status"] in ("refuted", "confirmed", "expired") and i,
                              "the investigation to be settled", 300)
        if inv:
            self.caption(n, f"Claim {inv['status']}", inv.get("finding") or "")
            print(f"   {inv['status'].upper()}: {inv.get('finding')}", flush=True)
        self.sim({"scouts": "clear"})
        self.tool("tools.attack", "restore", SUSPECT)
        self.pause("next: the kill switch", 14)

    # ---------- the real machine ----------
    def station(self) -> tuple[str, dict]:
        with self.state.lock:
            return self.state.station or ("uno-gas-01", {})

    def relay(self, unit: str, on: bool) -> None:
        self.operator(topics.command(unit), {
            "task_id": f"task-relay-{unit}-{int(time.time() * 1000)}", "unit_id": unit, "type": "relay",
            "params": {"on": on}, "status": "approved", "sandbox": False,
            "requested_by": "demo", "approved_by": "operator",
        })

    def step_hardware(self, n: int) -> None:
        unit, _ = self.station()
        self.say(n, "One of these machines is real",
                 f"{unit} is an Arduino in a 3D-printed box, with a gas sensor, a lid switch and a relay. It speaks "
                 "its own made-up format. The gateway worked out what it is from the shape of its data and signs "
                 "its readings like any other machine's.")
        self.pause("next: smoke in the box", 12)

        self.caption(n, "Smoke in the box",
                     "A little smoke drifts into the box. The gas sensor climbs several times above the clean air it "
                     f"measured at start-up, and {STATION_ZONE}'s verdict changes within seconds.")
        print("   Blow out a match near the box (never with the alcohol), or wait for the fake board's smoke.", flush=True)

        def smoky():
            # Wait for the verifier's verdict, not the box's latest reading: it judges gas on the median of
            # the last 10 s, so the first smoky reading alone doesn't change the zone yet.
            with self.state.lock:
                score = dict(self.state.scores.get(STATION_ZONE) or {})
            return score if (score.get("measured") or {}).get("gas_vs_clean_air", 0) >= 3 else None

        score = self.wait_until(smoky, "gas in the box", 150)
        if score:
            rec = score.get("recommendation", "unknown")
            self.caption(n, f"{STATION_ZONE} is now: {scoring_label(rec)}", score.get("verdict", ""))
            print(f"   {STATION_ZONE}: {rec}", flush=True)
        self.pause("next: a forged command", 10)

        self.caption(n, "A forged command goes nowhere",
                     f"Someone claims to be the operator and orders {unit}'s relay on. The signature isn't the "
                     "operator's, so the gateway refuses it and the relay never clicks. Watch the event feed.")
        self.tool("tools.spoof", "command", unit)
        print("   Sent a forged relay command", flush=True)
        self.pause("switch the relay on for real", 10)

        self.caption(n, "The operator switches on the air pump",
                     "The same order, signed with the operator key. The gateway checks the signature, translates it "
                     "into the box's own command and sends it down the USB cable. The relay clicks.")
        if not self.station()[1].get("relay_on"):
            self.relay(unit, True)
        on = self.wait_until(lambda: self.station()[1].get("relay_on"), "the relay to switch on", 20)
        print(f"   Relay {'on' if on else 'did not report on'}", flush=True)
        self.pause("next: the kill switch", 10)

    def step_killswitch(self, n: int) -> None:
        with self.state.lock:
            station = self.state.station
        self.say(n, "A kill switch for everything that moves",
                 "One signed operator message halts every robot and refuses new tasks, for every gateway, until it "
                 "is released." + (f" It also switches {station[0]}'s relay off." if station else ""))
        self.pause("engage it", 6)
        self.operator(topics.KILLSWITCH, {"engaged": True, "by": "operator"}, retain=True)
        self.pause("release it", 8)
        self.operator(topics.KILLSWITCH, {"engaged": False, "by": "operator"}, retain=True)

    def step_end(self, n: int) -> None:
        self.say(n, "The machines kept us out. Now they're building our way home.",
                 "Every verdict on the left came from signed measurements that anyone can check, not from the "
                 "Directorate's word.")
        self.pause("finish", 10)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", default=config.GATEWAY_ID)
    parser.add_argument("--auto", action="store_true", help="advance by itself instead of waiting for Enter")
    parser.add_argument("--start", type=int, default=1, metavar="N", help="skip to step N (to rehearse one part)")
    parser.add_argument("--keep-caption", action="store_true", help="leave the last caption on screen at the end")
    args = parser.parse_args()
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(errors="replace")   # a Windows console without UTF-8 can't print "CO₂"
    director = Director(args.gateway, args.auto, args.start)
    try:
        director.run()
        print("\nDemo finished.", flush=True)
    except KeyboardInterrupt:
        print("\nDemo stopped.", flush=True)
    finally:
        if not args.keep_caption:
            director.clear_caption()
        director.client.loop_stop()
        director.client.disconnect()


if __name__ == "__main__":
    main()
