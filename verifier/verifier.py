"""Verifier: cross-check readings, flag faulty sensors, score every zone, sign the result,
and investigate claims that nobody has confirmed.

    python -m tools.keygen verifier      # once
    python -m verifier.verifier

Only readings with a valid signature from a trusted gateway are considered.

A sensor is flagged faulty when it:
  - reads at its hardware maximum (saturated),
  - reports exactly the same value for several readings in a row (stuck),
  - keeps disagreeing with other machines a few metres away, or
  - was investigated and an independent machine measured something different (compromised).

A signature proves a reading wasn't altered; it can't prove the machine is honest. So when a
single, unconfirmed machine is what changes a zone's verdict, the verifier opens an
investigation: a request to send another machine to that spot. Once an independent machine
has measured there, the claim is confirmed or refuted.

Published (all signed, retained):
  uplink/zones/<zone>/score   zone verdicts
  uplink/investigations       recent investigations
  uplink/map/snapshot         everything surveyed so far, for late viewers
"""

import json
import logging
import math
import statistics
import threading
import time
from collections import defaultdict, deque

import paho.mqtt.client as paho

from hollow import geo, signing, topics
from hollow.mqtt import connect, make_client, now_iso

from . import scoring

log = logging.getLogger("verifier")

SIGNER_ID = "verifier"
WINDOW_S = 60             # neighbour cross-checks only use readings this recent
SCORE_WINDOW_S = 600      # zone conditions are judged on the last 10 minutes...
FRESH_S = 120             # ...preferring each block's readings from the last 2 minutes
NEIGHBOR_RADIUS = 30      # map units (15 m); real hazards fall off with distance, so stay close
MIN_NEIGHBOR_MACHINES = 2 # one other machine is not enough to overrule a sensor
DISAGREE_STREAK = 3       # a disagreement must persist this many readings (moving scouts cross gradients)
STUCK_N = 8               # identical readings in a row; real sensors jitter
COVER_BLOCK = 4           # zone coverage is counted in 4 x 4 cell blocks (20 m squares)
MIN_MEASURED = 0.25       # fraction of a zone that must be measured before any verdict
PUBLISH_EVERY_S = 3
SNAPSHOT_EVERY_S = 10
INVESTIGATE_RADIUS = 40   # map units (20 m) around the claim that count as "checking the spot"
INVESTIGATION_SAMPLES = 3 # independent readings needed to settle an investigation
INVESTIGATION_TTL_S = 900
HAZARDS = ("radiation_usv_h", "air_quality_aqi")
SENSOR_MAX = {"air_quality_aqi": 500, "radiation_cpm": 9999}
# quantity -> (absolute tolerance, ratio tolerance or None). Generous on purpose:
# we want to catch broken sensors, not argue with a real hotspot.
TOLERANCE = {"radiation_usv_h": (0.5, 5.0), "air_quality_aqi": (40, 2.5), "temp_c": (6, None)}
ZONES = [f"Z{i}" for i in range(1, geo.ZONE_COLS * geo.ZONE_ROWS + 1)]


def disagrees(q: str, value: float, reference: float) -> bool:
    abs_tol, ratio = TOLERANCE[q]
    if abs(value - reference) <= abs_tol:
        return False
    if ratio is None:
        return True
    lo, hi = sorted((max(value, 1e-6), max(reference, 1e-6)))
    return hi / lo > ratio


class Verifier:
    def __init__(self):
        self.key = signing.load_signing_key(SIGNER_ID)
        self.trusted = signing.load_trusted_keys()
        self.trusted.pop(SIGNER_ID, None)            # never trust our own key for readings
        self.lock = threading.Lock()                 # MQTT thread writes, main thread scores
        self.samples: deque = deque(maxlen=60000)    # (t, unit, x, y, zone, quantity, value)
        self.history = defaultdict(lambda: defaultdict(lambda: deque(maxlen=STUCK_N)))
        self.streak = defaultdict(int)               # unit -> consecutive neighbour disagreements
        self.kind: dict[str, str] = {}
        self.flags: dict[str, str] = {}              # unit_id -> reason (left out of scores)
        self.compromised: dict[str, str] = {}        # unit_id -> finding (permanent)
        self.corroborated: set[str] = set()          # units an investigation confirmed
        self.investigations: list[dict] = []
        self.next_inv = 1
        self.mapped = defaultdict(set)               # zone -> {(gx, gy)}
        self.cells: dict = {}                        # (gx, gy) -> [height_m, class, seen_epoch, scanner]
        self.client = make_client("verifier")
        self.client.on_connect = self.on_connect
        self.client.on_message = self.on_message

    # ---------- input ----------
    def on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code.is_failure:
            log.error("Connect failed: %s", reason_code)
            return
        client.subscribe([(topics.READINGS_ALL, 0), (topics.COMMANDS_ALL, 0)])
        log.info("Verifying readings from %s", ", ".join(sorted(self.trusted)) or "no trusted gateways")

    def on_message(self, client, userdata, msg: paho.MQTTMessage):
        with self.lock:
            if msg.topic.startswith("uplink/commands/"):
                self.on_command(msg)
            else:
                self.on_reading(msg)

    def on_command(self, msg) -> None:
        try:
            cmd = json.loads(msg.payload)
        except json.JSONDecodeError:
            return
        inv_id = (cmd.get("params") or {}).get("investigation")
        inv = next((i for i in self.investigations if i["id"] == inv_id), None)
        if inv and cmd.get("status") == "approved" and inv["status"] == "open":
            inv["status"] = "dispatched"
            inv["assigned"] = cmd.get("unit_id")
            log.info("%s: %s dispatched to check", inv["id"], inv["assigned"])

    def on_reading(self, msg) -> None:
        try:
            reading = signing.open_envelope(json.loads(msg.payload), self.trusted)
        except (ValueError, json.JSONDecodeError, KeyError, TypeError):
            return  # forged or tampered: the dashboard reports these; we just ignore them
        unit = reading["unit_id"]
        self.kind[unit] = reading.get("kind", "unknown")
        now = time.time()

        for cell in reading.get("survey", {}).get("cells", []):
            gx, gy, height, cls = cell
            self.mapped[geo.zone_for(gx * geo.CELL + 5, gy * geo.CELL + 5)].add((gx, gy))
            self.cells[(gx, gy)] = [height, cls, now, unit]

        loc = reading.get("location")
        values = reading.get("readings", {})
        for q, v in values.items():
            self.history[unit][q].append(v)
        if not loc:
            return
        zone = geo.zone_for(loc["x"], loc["y"])
        self.check(unit, loc, values, now)
        for q in scoring.QUANTITIES:
            if q in values:
                self.samples.append((now, unit, loc["x"], loc["y"], zone, q, float(values[q])))

    # ---------- fault detection ----------
    def recent(self, now: float, seconds: float):
        """Samples from the last `seconds`, newest first (samples are in time order)."""
        out = []
        for s in reversed(self.samples):
            if now - s[0] >= seconds:
                break
            out.append(s)
        return out

    def check(self, unit: str, loc: dict, values: dict, now: float) -> None:
        reasons = []
        if unit in self.compromised:
            reasons.append(self.compromised[unit])
        for q, maximum in SENSOR_MAX.items():
            if q in values and values[q] >= maximum:
                reasons.append(f"{q} pinned at the sensor maximum ({values[q]:g})")
        if self.kind.get(unit) != "scout_robot":  # moving units naturally repeat less
            for q in scoring.QUANTITIES:
                hist = self.history[unit][q]
                if len(hist) == STUCK_N and len(set(hist)) == 1:
                    reasons.append(f"{q} stuck at {hist[-1]:g} for {STUCK_N} readings")

        window = self.recent(now, WINDOW_S)
        disagreement = None
        for q in scoring.QUANTITIES:
            if q not in values:
                continue
            nearby = [
                s for s in window
                if s[1] != unit and s[5] == q and s[1] not in self.flags
                and math.hypot(s[2] - loc["x"], s[3] - loc["y"]) <= NEIGHBOR_RADIUS
            ]
            machines = len({s[1] for s in nearby})
            if len(nearby) >= 3 and machines >= MIN_NEIGHBOR_MACHINES:
                median = statistics.median(s[6] for s in nearby)
                if disagrees(q, float(values[q]), median):
                    disagreement = (
                        f"reads {q} {values[q]:g} but {machines} machines within 15 m measured about {median:.3g}"
                    )
        self.streak[unit] = self.streak[unit] + 1 if disagreement else 0
        if disagreement and self.streak[unit] >= DISAGREE_STREAK:
            reasons.append(disagreement)

        reason = "; ".join(reasons) if reasons else None
        if reason and unit not in self.flags:
            log.warning("FLAGGED %s: %s", unit, reason)
        elif not reason and unit in self.flags:
            log.info("Cleared %s", unit)
        if reason:
            self.flags[unit] = reason
        else:
            self.flags.pop(unit, None)

    # ---------- zone scores ----------
    @staticmethod
    def block_of(s) -> tuple[int, int]:
        return int(s[2] // geo.CELL) // COVER_BLOCK, int(s[3] // geo.CELL) // COVER_BLOCK

    def zone_conditions(self, zone_recent: list, now: float) -> dict[str, float]:
        """Judge each 20 m block on its own (median of its freshest readings), then the zone on
        its worst block. Every place counts once, however many machines report from it, so a
        hotspot seen by one fixed sensor isn't outvoted by fast-reporting scouts elsewhere."""
        by_block = defaultdict(lambda: defaultdict(list))
        for s in zone_recent:
            by_block[s[5]][self.block_of(s)].append((s[0], s[6]))
        measured = {}
        for q in scoring.QUANTITIES:
            per_block = []
            for readings in by_block[q].values():
                fresh = [v for t, v in readings if now - t < FRESH_S]
                per_block.append(statistics.median(fresh or [v for _, v in readings]))
            if per_block:
                measured[q] = round(max(per_block) if q in HAZARDS else statistics.median(per_block), 3)
        return measured

    def score_zone(self, zone: str, now: float, recent: list, exclude: frozenset = frozenset()) -> dict:
        zone_recent = [s for s in recent if s[4] == zone and s[1] not in self.flags and s[1] not in exclude]
        measured = self.zone_conditions(zone_recent, now)
        units = {s[1] for s in zone_recent}
        flagged = [
            {"unit_id": u, "reason": r} for u, r in sorted(self.flags.items())
            if any(s[1] == u and s[4] == zone for s in recent)
        ]
        zone_cells = (geo.GRID_W * geo.GRID_H) / len(ZONES)
        mapped_frac = min(1.0, len(self.mapped[zone]) / zone_cells)
        # How much of the zone has actually had its air and radiation measured?
        blocks = {self.block_of(s) for s in zone_recent}
        spatial = min(1.0, len(blocks) / (zone_cells / COVER_BLOCK ** 2))
        coverage = len(measured) / len(scoring.QUANTITIES)
        confidence = round(0.6 * spatial + 0.25 * coverage + 0.15 * min(1, len(units) / 3), 2)

        score, limiting = None, None
        if measured:
            subs = {q: scoring.subscore(q, v) for q, v in measured.items()}
            limiting = min(subs, key=subs.get)
            score = round(subs[limiting])
        # Never give an all-clear from a corner of a zone: need a quarter of it measured.
        enough = score is not None and spatial >= MIN_MEASURED and confidence >= 0.45
        rec = scoring.recommendation(score) if enough else "unknown"

        verdict = scoring.verdict(measured, rec, limiting, len(flagged), round(100 * spatial))
        open_inv = next(
            (i for i in self.investigations if i["zone"] == zone and i["status"] in ("open", "dispatched")), None
        )
        if open_inv:
            verdict += (
                f" Disputed: {open_inv['suspect']} alone reports {scoring.describe(open_inv['quantity'], open_inv['claimed'])}; "
                + ("a machine is on its way to check." if open_inv["status"] == "dispatched" else "waiting for someone to send a machine to check.")
            )
        return {
            "gateway_id": SIGNER_ID,
            "zone": zone,
            "score": score,
            "confidence": confidence,
            "recommendation": rec,
            "verdict": verdict,
            "limiting_factor": limiting,
            "measured": measured,
            "flagged": flagged,
            "unit_count": len(units),
            "mapped_pct": round(100 * mapped_frac),
            "measured_pct": round(100 * spatial),
            "investigation": open_inv["id"] if open_inv else None,
            "timestamp": now_iso(),
        }

    # ---------- investigations ----------
    def review_claims(self, now: float, recent: list) -> None:
        """Open an investigation when one unconfirmed machine is what changes a zone's verdict."""
        for zone in ZONES:
            if any(i["zone"] == zone and i["status"] in ("open", "dispatched") for i in self.investigations):
                continue
            zone_recent = [s for s in recent if s[4] == zone and s[1] not in self.flags]
            by_unit = defaultdict(list)
            for s in zone_recent:
                if s[5] in HAZARDS:
                    by_unit[(s[1], s[5])].append(s)
            for (unit, q), mine in by_unit.items():
                if unit in self.corroborated or any(i["suspect"] == unit for i in self.investigations):
                    continue
                fresh = [s for s in mine if now - s[0] < FRESH_S]
                if not fresh:
                    continue
                claimed = statistics.median(s[6] for s in fresh)
                if scoring.subscore(q, claimed) >= 80:
                    continue  # not claiming a hazard
                mine = fresh
                worst = max(mine, key=lambda s: s[6])
                others = [
                    s for s in recent
                    if s[1] != unit and s[5] == q and s[1] not in self.flags
                    and math.hypot(s[2] - worst[2], s[3] - worst[3]) <= INVESTIGATE_RADIUS
                ]
                if any(not disagrees(q, claimed, s[6]) for s in others):
                    continue  # another machine has measured the same thing: corroborated
                with_unit = self.score_zone(zone, now, recent)["recommendation"]
                without = self.score_zone(zone, now, recent, frozenset({unit}))["recommendation"]
                if with_unit == without or "unknown" in (with_unit, without):
                    continue  # doesn't change the hazard verdict; not worth a trip
                inv = {
                    "id": f"inv-{self.next_inv:04d}",
                    "zone": zone,
                    "suspect": unit,
                    "quantity": q,
                    "claimed": round(claimed, 3),
                    "target": {"x": round(worst[2]), "y": round(worst[3])},
                    "reason": (
                        f"{unit} alone reports {scoring.describe(q, claimed)}, which would change {zone} "
                        f"from {without} to {with_unit}"
                        + (f"; {len(others)} earlier readings nearby disagree" if others else "; no other machine has confirmed it")
                    ),
                    "status": "open",
                    "assigned": None,
                    "opened_at": now_iso(),
                    "opened_epoch": now,
                    "finding": None,
                }
                self.next_inv += 1
                self.investigations.append(inv)
                log.warning("OPENED %s: %s", inv["id"], inv["reason"])
                break

    def resolve_investigations(self, now: float, recent: list) -> None:
        for inv in self.investigations:
            if inv["status"] not in ("open", "dispatched"):
                continue
            if now - inv["opened_epoch"] > INVESTIGATION_TTL_S:
                inv["status"], inv["finding"] = "expired", "Nobody checked in time."
                continue
            t = inv["target"]
            checks = [
                s for s in recent
                if s[0] > inv["opened_epoch"] and s[1] != inv["suspect"] and s[5] == inv["quantity"]
                and s[1] not in self.flags and math.hypot(s[2] - t["x"], s[3] - t["y"]) <= INVESTIGATE_RADIUS
            ]
            if len(checks) < INVESTIGATION_SAMPLES:
                continue
            measured = statistics.median(s[6] for s in checks)
            checkers = ", ".join(sorted({s[1] for s in checks}))
            q, suspect = inv["quantity"], inv["suspect"]
            if disagrees(q, inv["claimed"], measured):
                inv["status"] = "refuted"
                inv["finding"] = (
                    f"{checkers} measured {scoring.describe(q, measured)} at the spot. "
                    f"{suspect}'s claim of {inv['claimed']:g} is false; it is now treated as compromised."
                )
                self.compromised[suspect] = (
                    f"compromised: claimed {q} {inv['claimed']:g}, but {checkers} measured {measured:.3g} at the spot"
                )
                self.flags[suspect] = self.compromised[suspect]
            else:
                inv["status"] = "confirmed"
                inv["finding"] = f"{checkers} measured {scoring.describe(q, measured)} at the spot. The reading is real."
                self.corroborated.add(suspect)
            inv["resolved_at"] = now_iso()
            log.warning("%s %s: %s", inv["id"], inv["status"].upper(), inv["finding"])

    # ---------- publishing ----------
    def publish(self) -> None:
        with self.lock:
            now = time.time()
            recent = self.recent(now, SCORE_WINDOW_S)
            self.resolve_investigations(now, recent)
            self.review_claims(now, recent)
            scores = [self.score_zone(zone, now, recent) for zone in ZONES]
            public = [{k: v for k, v in i.items() if k != "opened_epoch"} for i in self.investigations[-12:]]
        for score in scores:
            envelope = signing.seal(SIGNER_ID, self.key, score)
            self.client.publish(topics.zone_score(score["zone"]), json.dumps(envelope), retain=True)
        inv_msg = {"gateway_id": SIGNER_ID, "investigations": public, "timestamp": now_iso()}
        self.client.publish(topics.INVESTIGATIONS, json.dumps(signing.seal(SIGNER_ID, self.key, inv_msg)), retain=True)

    def publish_snapshot(self) -> None:
        """The whole surveyed map, retained, so a viewer who opens the page late sees everything."""
        with self.lock:
            now = time.time()
            cells = [
                [gx, gy, h, cls, round(now - seen), scanner]
                for (gx, gy), (h, cls, seen, scanner) in self.cells.items()
            ]
        snapshot = {"gateway_id": SIGNER_ID, "cell_size": geo.CELL, "cells": cells, "timestamp": now_iso()}
        self.client.publish(topics.MAP_SNAPSHOT, json.dumps(signing.seal(SIGNER_ID, self.key, snapshot)), retain=True)

    def run(self) -> None:
        connect(self.client)
        self.client.loop_start()
        last_snapshot = 0.0
        try:
            while True:
                time.sleep(PUBLISH_EVERY_S)
                self.publish()
                if time.time() - last_snapshot >= SNAPSHOT_EVERY_S:
                    self.publish_snapshot()
                    last_snapshot = time.time()
        finally:
            self.client.loop_stop()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s", datefmt="%H:%M:%S")
    try:
        Verifier().run()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
