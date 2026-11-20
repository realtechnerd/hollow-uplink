"""The verifier: which sensors it stops trusting, when it opens an investigation, how an
independent measurement settles it, and what it publishes."""

import json
import time
import unittest

from nacl.signing import SigningKey

from hollow import signing, topics
from verifier import verifier as V

from .helpers import GATEWAY, FakeClient, KeyRing, message

# Zone Z2 is x 267..533, y 0..250. The hijacked probe in the demo sits at (330, 80).
SUSPECT, AT = "mqtt-rad-04", (330, 80)


def block(x, y):
    return int(x // V.geo.CELL) // V.COVER_BLOCK, int(y // V.geo.CELL) // V.COVER_BLOCK


class VerifierTestCase(unittest.TestCase):
    def setUp(self):
        self.ring = KeyRing()
        self.ring.install(self)
        self.v = V.Verifier()
        self.v.client = self.client = FakeClient()

    def reading(self, unit: str, kind: str, values: dict, x: float | None = None, y: float | None = None,
                key: SigningKey | None = None, **extra) -> None:
        """A signed reading from the gateway, delivered the way the broker would."""
        loc = {"x": x, "y": y} if x is not None else None
        zone = V.geo.zone_for(x, y) if loc else "UNMAPPED"
        env = self.ring.seal(GATEWAY, {"unit_id": unit, "kind": kind, "zone": zone, "location": loc,
                                       "readings": values, **extra}, key=key)
        self.v.on_message(None, None, message(topics.reading(zone, unit), env))

    def samples(self, rows) -> None:
        """Put (t, unit, x, y, quantity, value) samples straight into the verifier, in time order."""
        for t, unit, x, y, q, value in sorted(rows):
            self.v.samples.append((t, unit, x, y, V.geo.zone_for(x, y), q, value))


class FaultySensors(VerifierTestCase):
    def test_a_sensor_pinned_at_its_maximum_is_flagged(self):
        self.reading("mqtt-rad-02", "radiation_probe", {"radiation_usv_h": 64.99, "radiation_cpm": 9999}, 210, 160)
        self.assertIn("pinned at the sensor maximum", self.v.flags["mqtt-rad-02"])

    def test_a_stuck_sensor_is_flagged(self):
        for _ in range(V.STUCK_N):
            self.reading("mqtt-wx-03", "weather_mast", {"temp_c": 21.0}, 400, 130)
        self.assertIn("stuck", self.v.flags["mqtt-wx-03"])

    def test_a_jittering_sensor_is_not_stuck(self):
        for i in range(V.STUCK_N * 2):
            self.reading("mqtt-wx-03", "weather_mast", {"temp_c": 21.0 + (i % 3) / 10}, 400, 130)
        self.assertNotIn("mqtt-wx-03", self.v.flags)

    def test_low_resolution_real_hardware_may_repeat_itself(self):
        # The Uno's DHT11 reports whole degrees; a calm room repeats them. That's not a fault.
        for _ in range(V.STUCK_N * 2):
            self.reading("uno-gas-01", "field_station", {"temp_c": 22.0, "gas_vs_clean_air": 1.0}, 430, 320)
        self.assertNotIn("uno-gas-01", self.v.flags)

    def test_a_sensor_that_keeps_disagreeing_with_its_neighbours_is_flagged(self):
        for i in range(3):
            self.reading("scout-01", "scout_robot", {"radiation_usv_h": 0.12}, 100 + i, 100)
            self.reading("scout-02", "scout_robot", {"radiation_usv_h": 0.13}, 105 + i, 105)
        for _ in range(V.DISAGREE_STREAK - 1):
            self.reading("mqtt-rad-09", "radiation_probe", {"radiation_usv_h": 3.0}, 110, 100)
            self.assertNotIn("mqtt-rad-09", self.v.flags)      # one odd reading is not enough
        self.reading("mqtt-rad-09", "radiation_probe", {"radiation_usv_h": 3.0}, 110, 100)
        self.assertIn("machines within 15 m measured", self.v.flags["mqtt-rad-09"])

    def test_a_flag_clears_when_the_sensor_recovers(self):
        self.reading("mqtt-rad-02", "radiation_probe", {"radiation_usv_h": 64.99, "radiation_cpm": 9999}, 210, 160)
        self.reading("mqtt-rad-02", "radiation_probe", {"radiation_usv_h": 0.12, "radiation_cpm": 18}, 210, 160)
        self.assertNotIn("mqtt-rad-02", self.v.flags)

    def test_forged_and_replayed_readings_are_not_evidence(self):
        self.reading("mqtt-rad-02", "radiation_probe", {"radiation_usv_h": 950.0}, 210, 160, key=SigningKey.generate())
        self.assertEqual(len(self.v.samples), 0)
        env = self.ring.seal(GATEWAY, {"unit_id": "scout-01", "kind": "scout_robot", "zone": "Z1",
                                       "location": {"x": 100, "y": 100}, "readings": {"radiation_usv_h": 0.12}})
        msg = message(topics.reading("Z1", "scout-01"), env)
        self.v.on_message(None, None, msg)
        self.v.on_message(None, None, msg)
        self.assertEqual(len(self.v.samples), 1)
        self.assertEqual(self.v.replays_rejected, 1)


class ZoneScores(VerifierTestCase):
    def test_no_verdict_from_a_corner_of_a_zone(self):
        now = time.time()
        self.samples([(now - 5, "scout-01", 300, 40, q, v) for q, v in
                      (("radiation_usv_h", 0.12), ("air_quality_aqi", 40), ("temp_c", 24))])
        score = self.v.score_zone("Z2", now, self.v.recent(now, V.SCORE_WINDOW_S))
        self.assertEqual(score["recommendation"], "unknown")
        self.assertIsNotNone(score["score"])           # measured, just not enough of it

    def test_flagged_sensors_are_left_out_of_the_score(self):
        now = time.time()
        self.surveyed_z2(now)
        self.samples([(now - 1, "mqtt-rad-02", 300, 100, "radiation_usv_h", 64.99)])
        recent = self.v.recent(now, V.SCORE_WINDOW_S)
        self.assertNotEqual(self.v.score_zone("Z2", now, recent)["recommendation"], "outposts")
        self.v.flags["mqtt-rad-02"] = "pinned"
        score = self.v.score_zone("Z2", now, recent)
        self.assertEqual(score["recommendation"], "outposts")
        self.assertIn("Ignoring 1 faulty sensor", score["verdict"])

    def surveyed_z2(self, now, skip=None):
        """Two scouts have measured every 20 m block of Z2 a minute ago: all normal."""
        rows = []
        for i, x in enumerate(range(280, 534, 40)):
            for y in range(20, 250, 40):
                if skip and block(x, y) == block(*skip):
                    continue
                unit = f"scout-0{1 + (i % 2)}"
                rows += [(now - 60, unit, x, y, "radiation_usv_h", 0.12), (now - 60, unit, x, y, "air_quality_aqi", 40),
                         (now - 60, unit, x, y, "temp_c", 24)]
        self.samples(rows)


class Investigations(ZoneScores):
    """A hijacked probe tells a believable lie; another machine is sent to check it."""

    def hijack(self, now, lie=8.0):
        self.surveyed_z2(now, skip=AT)
        honest = [(now - 115 + 5 * i, SUSPECT, *AT, "radiation_usv_h", 0.13) for i in range(10)]
        lies = [(now - 40 + 4 * i, SUSPECT, *AT, "radiation_usv_h", lie) for i in range(10)]
        self.samples(honest + lies)
        self.v.review_claims(now, self.v.recent(now, V.SCORE_WINDOW_S))
        return [i for i in self.v.investigations if i["suspect"] == SUSPECT]

    def check(self, now, value, unit="scout-03"):
        self.samples([(now + 1 + i, unit, AT[0] + 5, AT[1], "radiation_usv_h", value) for i in range(V.INVESTIGATION_SAMPLES)])
        later = now + 10
        self.v.resolve_investigations(later, self.v.recent(later, V.SCORE_WINDOW_S))

    def test_one_machine_changing_the_verdict_opens_an_investigation(self):
        [inv] = self.hijack(time.time())
        self.assertEqual(inv["status"], "open")
        self.assertEqual(inv["zone"], "Z2")
        self.assertEqual(inv["target"], {"x": AT[0], "y": AT[1]})
        self.assertIn("would change Z2 from outposts", inv["reason"])

    def test_the_claim_is_what_the_machine_says_now(self):
        # Its last readings, not a blend with what it said before it was hijacked (that gave "3.87").
        [inv] = self.hijack(time.time())
        self.assertEqual(inv["claimed"], 8.0)

    def test_an_independent_measurement_refutes_a_lie(self):
        now = time.time()
        self.hijack(now)
        self.check(now, 0.12)
        [inv] = self.v.investigations
        self.assertEqual(inv["status"], "refuted")
        self.assertIn("claim of 8 is false", inv["finding"])
        self.assertIn("compromised", self.v.flags[SUSPECT])
        self.assertIn(SUSPECT, self.v.compromised)

    def test_an_independent_measurement_confirms_a_real_hazard(self):
        now = time.time()
        self.hijack(now, lie=7.5)
        self.check(now, 7.2)
        [inv] = self.v.investigations
        self.assertEqual(inv["status"], "confirmed")
        self.assertIn(SUSPECT, self.v.corroborated)
        self.assertNotIn(SUSPECT, self.v.flags)

    def test_the_suspect_cannot_check_itself(self):
        now = time.time()
        self.hijack(now)
        self.check(now, 8.0, unit=SUSPECT)
        self.assertEqual(self.v.investigations[0]["status"], "open")

    def test_too_few_checks_settle_nothing(self):
        now = time.time()
        self.hijack(now)
        self.samples([(now + 1, "scout-03", *AT, "radiation_usv_h", 0.12)])
        self.v.resolve_investigations(now + 5, self.v.recent(now + 5, V.SCORE_WINDOW_S))
        self.assertEqual(self.v.investigations[0]["status"], "open")

    def test_unchecked_investigations_expire(self):
        now = time.time()
        self.hijack(now)
        later = now + V.INVESTIGATION_TTL_S + 1
        self.v.resolve_investigations(later, [])
        self.assertEqual(self.v.investigations[0]["status"], "expired")

    def test_an_operator_approval_marks_it_dispatched(self):
        now = time.time()
        [inv] = self.hijack(now)
        cmd = self.ring.seal(signing.OPERATOR, {"task_id": "task-x", "unit_id": "scout-03", "type": "investigate",
                                                "params": {"x": 330, "y": 80, "investigation": inv["id"]}, "status": "approved"})
        self.v.on_message(None, None, message(topics.command("scout-03"), cmd))
        self.assertEqual((inv["status"], inv["assigned"]), ("dispatched", "scout-03"))

    def test_a_forged_approval_changes_nothing(self):
        now = time.time()
        [inv] = self.hijack(now)
        cmd = self.ring.seal(signing.OPERATOR, {"task_id": "task-x", "unit_id": "scout-03", "type": "investigate",
                                                "params": {"investigation": inv["id"]}, "status": "approved"},
                             key=SigningKey.generate())
        self.v.on_message(None, None, message(topics.command("scout-03"), cmd))
        self.assertEqual(inv["status"], "open")

    def test_a_claim_that_doesnt_change_the_verdict_isnt_worth_a_trip(self):
        self.assertEqual(self.hijack(time.time(), lie=0.2), [])

    def test_gas_claims_are_not_investigated(self):
        # No machine can be sent to re-measure gas, so a gas reading is scored but never "disputed".
        now = time.time()
        self.surveyed_z2(now, skip=AT)
        self.samples([(now - 5 + i, "uno-gas-01", *AT, "gas_vs_clean_air", 4.5) for i in range(5)])
        recent = self.v.recent(now, V.SCORE_WINDOW_S)
        self.v.review_claims(now, recent)
        self.assertEqual(self.v.investigations, [])
        self.assertEqual(self.v.score_zone("Z2", now, recent)["limiting_factor"], "gas_vs_clean_air")


class Publishing(VerifierTestCase):
    def test_map_layers_never_paint_from_a_flagged_sensor(self):
        self.reading("scout-01", "scout_robot", {"co2_ppm": 2400.0, "radiation_usv_h": 0.12}, 225, 330)
        self.reading("mqtt-rad-02", "radiation_probe", {"radiation_usv_h": 64.99, "radiation_cpm": 9999}, 210, 160)
        layers = self.v.layer_measures(time.time())
        self.assertEqual(layers["quantities"]["co2_ppm"][0][:3], [22, 33, 2400.0])
        painted_by = {layers["units"][row[4]] for q in layers["quantities"].values() for row in q}
        self.assertEqual(painted_by, {"scout-01"})

    def test_snapshot_and_scores_are_signed_by_the_verifier(self):
        self.reading("scout-01", "scout_robot", {"radiation_usv_h": 0.12}, 125, 85,
                     survey={"cell_size": 10, "cells": [[12, 8, 0.0, "open"]]})
        self.v.publish_snapshot()
        self.v.publish()
        [snap] = self.client.on(topics.MAP_SNAPSHOT)
        opened = signing.open_envelope(snap, self.ring.trusted, role=signing.VERIFIER)
        self.assertEqual(opened["cells"][0][:4], [12, 8, 0.0, "open"])
        scores = self.client.on("uplink/zones/")
        self.assertEqual(len(scores), len(V.ZONES))
        for env in scores:
            signing.open_envelope(env, self.ring.trusted, role=signing.VERIFIER)
        retained = {topic: retain for topic, _, retain in self.client.published}
        self.assertTrue(retained[topics.MAP_SNAPSHOT] and retained[topics.INVESTIGATIONS])


if __name__ == "__main__":
    unittest.main()
