"""Signed envelopes (hollow/signing.py) and replay protection (hollow/replay.py)."""

import json
import time
import unittest

from nacl.signing import SigningKey

from hollow import replay, signing

from .helpers import GATEWAY, KeyRing, epoch_iso


class SignedEnvelopes(unittest.TestCase):
    def setUp(self):
        self.ring = KeyRing()

    def test_round_trip_returns_the_original_message(self):
        env = self.ring.seal(GATEWAY, {"readings": {"temp_c": 21.5}, "note": "µSv/h ünïcode"})
        msg = signing.open_envelope(env, self.ring.trusted)
        self.assertEqual(msg["readings"], {"temp_c": 21.5})
        self.assertEqual(msg["note"], "µSv/h ünïcode")

    def test_edited_payload_is_rejected(self):
        env = self.ring.seal(GATEWAY, {"readings": {"radiation_usv_h": 0.2}})
        tampered = json.loads(env["payload"])
        tampered["readings"]["radiation_usv_h"] = 950.0
        env["payload"] = json.dumps(tampered, separators=(",", ":"))
        with self.assertRaisesRegex(ValueError, "bad signature"):
            signing.open_envelope(env, self.ring.trusted)

    def test_reformatting_the_payload_breaks_the_signature(self):
        # The exact signed string is shipped; re-serializing (even equivalently) must not verify.
        env = self.ring.seal(GATEWAY, {"readings": {"temp_c": 21.5}})
        env["payload"] = json.dumps(json.loads(env["payload"]), indent=1)
        with self.assertRaisesRegex(ValueError, "bad signature"):
            signing.open_envelope(env, self.ring.trusted)

    def test_key_nobody_trusts_is_rejected(self):
        env = self.ring.seal("gw-stranger", {"readings": {}}, key=SigningKey.generate())
        with self.assertRaisesRegex(ValueError, "untrusted signer"):
            signing.open_envelope(env, self.ring.trusted)

    def test_impersonating_a_trusted_signer_with_another_key_is_rejected(self):
        env = self.ring.seal(GATEWAY, {"readings": {}}, key=SigningKey.generate())
        with self.assertRaisesRegex(ValueError, "bad signature"):
            signing.open_envelope(env, self.ring.trusted)

    def test_each_role_only_signs_its_own_messages(self):
        reading = self.ring.seal(GATEWAY, {"readings": {}})
        command = self.ring.seal(signing.OPERATOR, {"type": "relay"})
        self.assertTrue(signing.open_envelope(reading, self.ring.trusted, role="gateway"))
        self.assertTrue(signing.open_envelope(command, self.ring.trusted, role=signing.OPERATOR))
        with self.assertRaisesRegex(ValueError, "may not sign"):
            signing.open_envelope(reading, self.ring.trusted, role=signing.OPERATOR)   # a gateway can't give orders
        with self.assertRaisesRegex(ValueError, "may not sign"):
            signing.open_envelope(command, self.ring.trusted, role="gateway")         # the operator can't forge readings

    def test_signer_inside_the_payload_must_match_the_envelope(self):
        message = {"gateway_id": signing.OPERATOR, "type": "relay"}
        env = signing.seal(GATEWAY, self.ring.keys[GATEWAY], message)   # envelope says gateway, payload says operator
        with self.assertRaisesRegex(ValueError, "does not match"):
            signing.open_envelope(env, self.ring.trusted)

    def test_role_of(self):
        self.assertEqual(signing.role_of("verifier"), "verifier")
        self.assertEqual(signing.role_of("operator"), "operator")
        self.assertEqual(signing.role_of("gw-va"), "gateway")


class ReplayProtection(unittest.TestCase):
    def setUp(self):
        self.guard = replay.ReplayGuard()
        self.stamper = replay.Stamper()

    def stamped(self, seconds_ago: float = 0) -> dict:
        return self.stamper.stamp({"timestamp": epoch_iso(seconds_ago)})

    def test_new_messages_are_accepted_in_order(self):
        for _ in range(3):
            self.assertTrue(self.guard.check("reading/a", self.stamped()))

    def test_same_message_twice_is_a_replay(self):
        msg = self.stamped()
        self.guard.check("reading/a", msg)
        with self.assertRaisesRegex(ValueError, "replayed"):
            self.guard.check("reading/a", dict(msg))

    def test_older_sequence_number_is_a_replay(self):
        old, new = self.stamped(), self.stamped()
        self.guard.check("reading/a", new)
        with self.assertRaisesRegex(ValueError, "replayed"):
            self.guard.check("reading/a", old)

    def test_streams_are_tracked_separately(self):
        msg = self.stamped()
        self.assertTrue(self.guard.check("reading/a", msg))
        self.assertTrue(self.guard.check("reading/b", dict(msg)))

    def test_a_restarted_sender_starts_a_new_sequence(self):
        self.guard.check("reading/a", self.stamped())
        restarted = replay.Stamper()
        self.assertTrue(self.guard.check("reading/a", restarted.stamp({"timestamp": epoch_iso(0)})))

    def test_stale_message_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "stale"):
            self.guard.check("reading/a", self.stamped(seconds_ago=replay.MAX_AGE_S + 30))

    def test_message_from_the_future_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "future"):
            self.guard.check("reading/a", self.stamped(seconds_ago=-(replay.MAX_SKEW_S + 30)))

    def test_retained_duplicate_is_ignored_not_rejected(self):
        # The broker re-sends retained messages on reconnect; that's not an attack.
        msg = self.stamped()
        self.guard.check("zones/Z1", msg, retained=True)
        self.assertFalse(self.guard.check("zones/Z1", dict(msg), retained=True))

    def test_missing_sequence_number_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "missing"):
            self.guard.check("reading/a", {"timestamp": epoch_iso(0)})

    def test_now_can_be_given(self):
        msg = self.stamped()
        later = time.time() + replay.MAX_AGE_S + 5
        with self.assertRaisesRegex(ValueError, "stale"):
            self.guard.check("reading/a", msg, now=later)


if __name__ == "__main__":
    unittest.main()
