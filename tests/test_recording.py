"""The committed demo recording (dashboard/recordings/demo.jsonl.gz) that the dashboards replay
when there's no broker. A bad re-record should fail here, not on stage."""

import gzip
import json
import unittest
from pathlib import Path

from hollow import signing

RECORDING = Path(__file__).resolve().parent.parent / "dashboard" / "recordings" / "demo.jsonl.gz"


@unittest.skipUnless(RECORDING.exists(), "no recording (make one with: python run.py --demo --auto --record)")
class DemoRecording(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        lines = gzip.open(RECORDING, "rt", encoding="utf-8").read().splitlines()
        cls.header = json.loads(lines[0])
        cls.records = [json.loads(line) for line in lines[1:]]

    def test_header_carries_the_keys_it_was_signed_with(self):
        self.assertEqual(self.header["type"], "header")
        self.assertEqual(self.header["version"], 1)
        self.assertTrue({"verifier", "operator"} <= set(self.header["trusted"]))

    def test_messages_are_in_time_order_and_only_dashboard_topics(self):
        times = [r["t"] for r in self.records]
        self.assertEqual(times, sorted(times))
        for r in self.records:
            self.assertTrue(r["topic"].startswith("uplink/") or r["topic"] == "demo/caption", r["topic"])
            self.assertFalse(r["topic"].startswith("uplink/keys/"))

    def test_every_verifier_and_operator_message_verifies(self):
        for r in self.records:
            if r["topic"].startswith(("uplink/zones/", "uplink/map/", "uplink/investigations", "uplink/control/")):
                signing.open_envelope(json.loads(r["payload"]), self.header["trusted"])

    def test_only_the_staged_forgeries_fail_their_signature(self):
        failed = []
        for r in self.records:
            if r["topic"].startswith(("uplink/readings/", "uplink/commands/")):
                try:
                    signing.open_envelope(json.loads(r["payload"]), self.header["trusted"])
                except ValueError:
                    failed.append(r["topic"].rsplit("/", 1)[-1])
        # Step 3's forged and tampered readings, and step 9's forged relay command (if the board was there).
        self.assertIn(sorted(failed), (["mqtt-air-01", "mqtt-wx-03"], ["mqtt-air-01", "mqtt-wx-03", "uno-gas-01"]))

    def test_it_tells_the_whole_story(self):
        titles = [json.loads(r["payload"])["title"] for r in self.records if r["topic"] == "demo/caption" and r["payload"]]
        self.assertTrue(titles[0].startswith("Nobody has seen the surface"))
        self.assertIn("The machines kept us out", titles[-1])
        findings = [i.get("finding") or "" for r in self.records if r["topic"] == "uplink/investigations"
                    for i in json.loads(json.loads(r["payload"])["payload"])["investigations"]]
        self.assertTrue(any("is false" in f for f in findings), "the hijacked probe is never refuted")


if __name__ == "__main__":
    unittest.main()
