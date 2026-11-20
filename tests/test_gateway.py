"""The edge gateway end to end, minus the network: raw machine messages in, signed readings out,
and the rules for which commands ever reach a machine."""

import json
import random
import unittest
from pathlib import Path

from nacl.signing import SigningKey

from gateway.gateway import Gateway
from hollow import signing, topics
from simulators import mqtt_sensor
from simulators.world import environment

from .helpers import GATEWAY, FakeClient, KeyRing, message
from .test_adapters import scout_raw, uno_line

ENV = environment(400, 130, 0, random.Random(4))
EXAMPLE_UNITS = json.loads((Path(__file__).resolve().parent.parent / "gateway" / "config.example.json").read_text())["units"]


class GatewayTestCase(unittest.TestCase):
    def setUp(self):
        self.ring = KeyRing()
        self.ring.install(self)
        self.gw = Gateway(GATEWAY)
        self.gw.unit_map = EXAMPLE_UNITS          # not a teammate's own gateway/config.json
        self.gw.client = self.client = FakeClient()

    def raw(self, unit: str, payload: dict) -> None:
        self.gw.on_message(None, None, message(topics.raw(GATEWAY, "mqtt", unit), payload))

    def readings(self, unit: str) -> list[dict]:
        out = []
        for topic, payload, _ in self.client.published:
            if topic.startswith("uplink/readings/") and topic.endswith(f"/{unit}"):
                out.append(signing.open_envelope(json.loads(payload), self.ring.trusted, role="gateway"))
        return out

    def command(self, unit: str, kind: str, params: dict, key: SigningKey | None = None, task_id: str = "t1",
                status: str = "approved") -> None:
        env = self.ring.seal(signing.OPERATOR, {
            "task_id": task_id, "unit_id": unit, "type": kind, "params": params, "status": status, "sandbox": False,
        }, key=key)
        self.gw.on_message(None, None, message(topics.command(unit), env))

    def machine_commands(self, unit: str) -> list[dict]:
        return self.client.on(topics.machine_command(GATEWAY, "mqtt", unit))

    def acks(self, unit: str) -> list[dict]:
        return [signing.open_envelope(e, self.ring.trusted) for e in self.client.on(topics.ack(unit))]


class Readings(GatewayTestCase):
    def test_a_new_machine_is_discovered_then_its_readings_are_signed(self):
        self.raw("mqtt-wx-03", mqtt_sensor.weather_mast(ENV, random.Random(5)))
        discovered = self.client.on(topics.DISCOVERED)
        self.assertEqual(discovered[0]["fingerprint"]["kind"], "weather_mast")
        [reading] = self.readings("mqtt-wx-03")
        self.assertEqual(reading["kind"], "weather_mast")
        self.assertEqual(reading["zone"], "Z2")                                  # from gateway config
        self.assertEqual(reading["location"], {"x": 400, "y": 130})
        self.assertAlmostEqual(reading["readings"]["temp_c"], ENV["temp_c"], delta=0.06)   # °F translated
        self.assertIn("seq", reading)                                             # replay protection

    def test_a_machine_is_only_discovered_once(self):
        for _ in range(3):
            self.raw("mqtt-air-01", mqtt_sensor.air_sensor(ENV, random.Random(6)))
        self.assertEqual(len(self.client.on(topics.DISCOVERED)), 1)
        self.assertEqual(len(self.readings("mqtt-air-01")), 3)

    def test_moving_machines_place_themselves(self):
        self.raw("scout-01", scout_raw())
        [reading] = self.readings("scout-01")
        self.assertEqual(reading["location"], {"x": 125.0, "y": 85.0})
        self.assertEqual(reading["zone"], "Z1")
        self.assertEqual(len(reading["survey"]["cells"]), 2)

    def test_unplaced_machines_are_unmapped(self):
        self.raw("mqtt-air-77", mqtt_sensor.air_sensor(ENV, random.Random(7)))
        [reading] = self.readings("mqtt-air-77")
        self.assertEqual(reading["zone"], "UNMAPPED")
        self.assertIsNone(reading["location"])

    def test_unknown_machines_are_discovered_but_never_reported(self):
        self.raw("mystery-1", {"blob": 1})
        self.assertEqual(self.client.on(topics.DISCOVERED)[0]["fingerprint"]["kind"], "unknown")
        self.assertEqual(self.readings("mystery-1"), [])

    def test_unparseable_payloads_are_ignored(self):
        self.gw.on_message(None, None, message(topics.raw(GATEWAY, "mqtt", "x"), b"\xff not json"))
        self.assertEqual(self.client.published, [])

    def test_a_scout_arriving_on_a_task_is_acknowledged_once(self):
        for _ in range(2):
            self.raw("scout-02", scout_raw(task={"ref": "task-inv-0001", "state": "arrived"}))
        self.assertEqual([a["result"] for a in self.acks("scout-02")], ["done"])


class CommandsReachMachinesOnlyWhenTheOperatorSigned(GatewayTestCase):
    def setUp(self):
        super().setUp()
        self.raw("uno-gas-01", uno_line())
        self.raw("scout-01", scout_raw())

    def test_operator_signed_relay_command_is_sent_in_the_boxs_own_format(self):
        self.command("uno-gas-01", "relay", {"on": True})
        self.assertEqual(self.machine_commands("uno-gas-01"), [{"op": "relay", "on": True, "ref": "t1"}])
        self.assertEqual(self.acks("uno-gas-01")[-1]["result"], "accepted")

    def test_forged_command_never_reaches_the_machine(self):
        self.command("uno-gas-01", "relay", {"on": True}, key=SigningKey.generate())
        self.assertEqual(self.machine_commands("uno-gas-01"), [])
        self.assertEqual(self.acks("uno-gas-01"), [])

    def test_a_command_signed_by_a_gateway_key_is_not_an_order(self):
        env = self.ring.seal(GATEWAY, {"task_id": "g", "unit_id": "uno-gas-01", "type": "relay",
                                       "params": {"on": True}, "status": "approved"})
        self.gw.on_message(None, None, message(topics.command("uno-gas-01"), env))
        self.assertEqual(self.machine_commands("uno-gas-01"), [])

    def test_only_approved_tasks_run(self):
        self.command("uno-gas-01", "relay", {"on": True}, status="requested")
        self.assertEqual(self.machine_commands("uno-gas-01"), [])

    def test_each_task_runs_once(self):
        self.command("uno-gas-01", "relay", {"on": True}, task_id="same")
        self.command("uno-gas-01", "relay", {"on": True}, task_id="same")
        self.assertEqual(len(self.machine_commands("uno-gas-01")), 1)

    def test_replayed_command_is_ignored(self):
        env = self.ring.seal(signing.OPERATOR, {"task_id": "r1", "unit_id": "uno-gas-01", "type": "relay",
                                                "params": {"on": True}, "status": "approved"})
        msg = message(topics.command("uno-gas-01"), env)
        self.gw.on_message(None, None, msg)
        self.gw.handled_tasks.clear()             # even if the task id were forgotten, the sequence number isn't
        self.gw.on_message(None, None, msg)
        self.assertEqual(len(self.machine_commands("uno-gas-01")), 1)

    def test_a_machine_that_cant_do_the_task_says_so(self):
        self.command("uno-gas-01", "investigate", {"x": 1, "y": 1})
        self.assertEqual(self.machine_commands("uno-gas-01"), [])
        self.assertIn("can't do", self.acks("uno-gas-01")[-1]["reason"])

    def test_commands_for_other_sites_are_ignored(self):
        self.command("scout-99", "investigate", {"x": 1, "y": 1})
        self.assertEqual(self.client.on("surface/cmd/"), [])


class KillSwitch(GatewayTestCase):
    def setUp(self):
        super().setUp()
        self.raw("uno-gas-01", uno_line())
        self.raw("scout-01", scout_raw())
        self.raw("mqtt-air-01", mqtt_sensor.air_sensor(ENV, random.Random(8)))

    def engage(self, on: bool = True, key: SigningKey | None = None) -> None:
        env = self.ring.seal(signing.OPERATOR, {"engaged": on, "by": "operator"}, key=key)
        self.gw.on_message(None, None, message(topics.KILLSWITCH, env))

    def test_halts_robots_and_switches_relays_off(self):
        self.engage()
        self.assertEqual(self.machine_commands("scout-01"), [{"op": "halt", "ref": "killswitch"}])
        self.assertEqual(self.machine_commands("uno-gas-01"), [{"op": "relay", "on": False, "ref": "killswitch"}])
        self.assertEqual(self.machine_commands("mqtt-air-01"), [])     # sensors have nothing to stop

    def test_refuses_new_tasks_until_released(self):
        self.engage()
        self.command("uno-gas-01", "relay", {"on": True}, task_id="during")
        self.assertEqual(self.acks("uno-gas-01")[-1]["reason"], "kill switch engaged")
        self.engage(False)
        self.command("uno-gas-01", "relay", {"on": True}, task_id="after")
        self.assertEqual(self.acks("uno-gas-01")[-1]["result"], "accepted")

    def test_a_forged_kill_switch_does_nothing(self):
        self.engage(key=SigningKey.generate())
        self.assertFalse(self.gw.killswitch)
        self.assertEqual(self.client.on("surface/cmd/"), [])


if __name__ == "__main__":
    unittest.main()
