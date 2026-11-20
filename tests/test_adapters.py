"""Protocol adapters: recognising each vendor and translating it into the common format.

Raw messages come from the simulators themselves (and the Uno bridge's stand-in board), so
a change to a vendor format on one side and not the other fails here, not on stage.
"""

import json
import random
import unittest

from gateway.adapters.modbus_adapter import ModbusAdapter, parse_targets
from gateway.adapters.mqtt_adapter import MqttAdapter
from simulators import modbus_device, mqtt_sensor
from simulators.world import environment
from tools.uno_bridge import FakeUno

ENV = environment(470, 420, 0, random.Random(1))   # by the landfill fire: values well away from defaults


def scout_raw(env: dict = ENV, hits=None, task=None) -> dict:
    """What simulators/scout.py publishes (SCOUT-MK3 format)."""
    raw = {
        "model": "SCOUT-MK3",
        "pose": {"x": 125.0, "y": 85.0, "theta": 90.0},
        "battery": 97.5,
        "scan": {"cell": 10, "hits": hits if hits is not None else [[12, 8, 0.0, 0], [13, 8, 24.5, 2]]},
        "env": {
            "rad": env["radiation_usv_h"], "aqi": env["aqi"], "temp": env["temp_c"],
            "pm25": env["pm25_ugm3"], "co2": env["co2_ppm"], "rh": env["humidity_pct"], "wind": env["wind_kph"],
        },
    }
    if task:
        raw["task"] = task
    return raw


def uno_line(warm: bool = True) -> dict:
    board = FakeUno(warmup_s=0 if warm else 3600)
    board.next_at = 0            # don't wait for the board's 2 s reporting period
    if warm:
        board.base = 180         # skip the "# gas baseline" log line it prints once warm
    return json.loads(board.readline())


class MqttFingerprints(unittest.TestCase):
    def setUp(self):
        self.a = MqttAdapter()
        self.rng = random.Random(2)

    def test_each_simulated_vendor_is_recognised_from_its_shape(self):
        cases = {
            "air_sensor": mqtt_sensor.air_sensor(ENV, self.rng),
            "radiation_probe": mqtt_sensor.radiation_probe(ENV, self.rng),
            "weather_mast": mqtt_sensor.weather_mast(ENV, self.rng),
            "scout_robot": scout_raw(),
            "field_station": uno_line(),
        }
        for kind, raw in cases.items():
            with self.subTest(kind=kind):
                fp = self.a.fingerprint(raw)
                self.assertEqual(fp.kind, kind)
                self.assertGreaterEqual(fp.confidence, 0.5)

    def test_unrecognised_payloads_are_unknown(self):
        self.assertEqual(self.a.fingerprint({"foo": 1, "bar": {"baz": 2}}).kind, "unknown")
        self.assertEqual(self.a.fingerprint([1, 2, 3]).kind, "unknown")
        self.assertEqual(self.a.fingerprint("hello").kind, "unknown")


class MqttTranslation(unittest.TestCase):
    def setUp(self):
        self.a = MqttAdapter()
        self.rng = random.Random(3)

    def test_air_sensor(self):
        raw = mqtt_sensor.air_sensor(ENV, self.rng)
        self.assertEqual(self.a.translate("air_sensor", raw), {
            "pm25_ugm3": ENV["pm25_ugm3"], "co2_ppm": ENV["co2_ppm"], "temp_c": ENV["temp_c"], "air_quality_aqi": ENV["aqi"],
        })

    def test_weather_mast_converts_fahrenheit(self):
        out = self.a.translate("weather_mast", mqtt_sensor.weather_mast(ENV, self.rng))
        self.assertAlmostEqual(out["temp_c"], ENV["temp_c"], delta=0.06)
        self.assertEqual(out["humidity_pct"], ENV["humidity_pct"])
        self.assertEqual(out["wind_kph"], ENV["wind_kph"])

    def test_radiation_probe_only_trusts_microsieverts(self):
        raw = mqtt_sensor.radiation_probe(ENV, self.rng)
        self.assertEqual(self.a.translate("radiation_probe", raw)["radiation_usv_h"], ENV["radiation_usv_h"])
        other_units = {**raw, "unit": "mR/h"}
        out = self.a.translate("radiation_probe", other_units)
        self.assertNotIn("radiation_usv_h", out)
        self.assertIn("radiation_cpm", out)       # a count is still a count

    def test_scout_reports_all_seven_quantities_and_battery(self):
        out = self.a.translate("scout_robot", scout_raw())
        self.assertEqual(out, {
            "battery_pct": 97.5, "radiation_usv_h": ENV["radiation_usv_h"], "air_quality_aqi": ENV["aqi"],
            "temp_c": ENV["temp_c"], "pm25_ugm3": ENV["pm25_ugm3"], "co2_ppm": ENV["co2_ppm"],
            "humidity_pct": ENV["humidity_pct"], "wind_kph": ENV["wind_kph"],
        })

    def test_bad_values_are_dropped_not_fatal(self):
        raw = mqtt_sensor.air_sensor(ENV, self.rng)
        raw["co2_ppm"] = "n/a"
        out = self.a.translate("air_sensor", raw)
        self.assertNotIn("co2_ppm", out)
        self.assertIn("air_quality_aqi", out)

    def test_scout_extras_location_survey_and_task(self):
        hits = [[12, 8, 0.0, 0], [13, 8, 24.5, 2], ["x", 1], [1, 2, 3, 99]]   # last two are malformed
        ex = self.a.extras("scout_robot", scout_raw(hits=hits, task={"ref": "task-inv-0001", "state": "arrived"}))
        self.assertEqual(ex["location"], {"x": 125.0, "y": 85.0})
        self.assertEqual(ex["heading_deg"], 90.0)
        self.assertEqual(ex["survey"]["cells"], [[12, 8, 0.0, "open"], [13, 8, 24.5, "building"]])
        self.assertEqual(ex["task"], {"task_id": "task-inv-0001", "state": "arrived"})
        self.assertEqual(self.a.extras("air_sensor", {}), {})


class FieldStation(unittest.TestCase):
    """The real Arduino (hardware/uno_station), via the bridge's stand-in board."""

    def setUp(self):
        self.a = MqttAdapter()

    def test_gas_is_relative_to_the_boards_own_clean_air(self):
        raw = uno_line()
        out = self.a.translate("field_station", raw)
        self.assertAlmostEqual(out["gas_vs_clean_air"], raw["gas"]["adc"] / raw["gas"]["base"], places=2)
        self.assertEqual(out["humidity_pct"], 45)
        self.assertEqual(out["relay_on"], 0)
        self.assertIn("hatch_open", out)
        self.assertIn("light_pct", out)

    def test_ds18b20_temperature_wins_over_the_dht11(self):
        raw = uno_line()
        self.assertEqual(self.a.translate("field_station", raw)["temp_c"], raw["ds"]["t"])
        del raw["ds"]
        self.assertEqual(self.a.translate("field_station", raw)["temp_c"], raw["dht"]["t"])

    def test_no_gas_reading_while_the_sensor_warms_up(self):
        self.assertNotIn("gas_vs_clean_air", self.a.translate("field_station", uno_line(warm=False)))

    def test_smoke_reads_as_several_times_clean_air(self):
        raw = uno_line()
        raw["gas"]["adc"] = raw["gas"]["base"] * 4.5
        self.assertAlmostEqual(self.a.translate("field_station", raw)["gas_vs_clean_air"], 4.5)


class Commands(unittest.TestCase):
    """Approved common-format commands -> each machine's own vendor command."""

    def setUp(self):
        self.a = MqttAdapter()

    def test_scout_goto_halt_and_zone(self):
        cmd = self.a.command("scout_robot", {"type": "investigate", "task_id": "t1", "params": {"x": 330, "y": 80}})
        self.assertEqual(cmd, {"op": "goto", "x": 330.0, "y": 80.0, "ref": "t1"})
        self.assertEqual(self.a.command("scout_robot", {"type": "stop", "task_id": "k"}), {"op": "halt", "ref": "k"})
        zone = self.a.command("scout_robot", {"type": "scout_zone", "task_id": "z", "params": {"zone": "Z5"}})
        self.assertEqual(zone["op"], "goto")
        self.assertEqual((zone["x"], round(zone["y"])), (400.0, 375))   # centre of Z5

    def test_field_station_relay_and_kill_switch(self):
        on = self.a.command("field_station", {"type": "relay", "task_id": "r", "params": {"on": True}})
        self.assertEqual(on, {"op": "relay", "on": True, "ref": "r"})
        self.assertEqual(self.a.command("field_station", {"type": "stop", "task_id": "k"})["on"], False)
        self.assertIsNone(self.a.command("field_station", {"type": "investigate", "params": {"x": 1, "y": 1}}))

    def test_sensors_take_no_commands(self):
        for kind in ("air_sensor", "radiation_probe", "weather_mast"):
            self.assertIsNone(self.a.command(kind, {"type": "stop"}))
        self.assertIsNone(ModbusAdapter().command("radiation_probe", {"type": "stop"}))


class Modbus(unittest.TestCase):
    """Registers written by the simulated devices must decode back to the same values."""

    def setUp(self):
        self.a = ModbusAdapter()

    def raw(self, kind: str, faulty=False, lie=None) -> dict:
        identity = dict(modbus_device.MODELS[kind])
        regs = modbus_device.REGISTERS[kind](ENV, faulty, lie)
        return {"identity": identity, "registers": regs + [0] * (modbus_device.REGISTER_COUNT - len(regs))}

    def test_devices_are_identified_by_vendor_and_product_code(self):
        self.assertEqual(self.a.fingerprint(self.raw("rad")).kind, "radiation_probe")
        self.assertEqual(self.a.fingerprint(self.raw("air")).kind, "air_sensor")
        self.assertEqual(self.a.fingerprint({"identity": {"VendorName": "Acme", "ProductCode": "X"}}).kind, "unknown")
        self.assertEqual(self.a.fingerprint({"identity": {}}).kind, "unknown")

    def test_kestrel_radiation_probe_32_bit_dose(self):
        out = self.a.translate("radiation_probe", self.raw("rad"))
        self.assertAlmostEqual(out["radiation_usv_h"], ENV["radiation_usv_h"], places=3)
        self.assertAlmostEqual(out["temp_c"], ENV["temp_c"], places=1)

    def test_kestrel_fault_bit_means_no_reading(self):
        raw = self.raw("rad")
        raw["registers"][4] = 0
        self.assertEqual(self.a.translate("radiation_probe", raw), {})

    def test_halden_word_swapped_floats(self):
        out = self.a.translate("air_sensor", self.raw("air"))
        self.assertAlmostEqual(out["air_quality_aqi"], ENV["aqi"], delta=0.1)
        self.assertAlmostEqual(out["pm25_ugm3"], ENV["pm25_ugm3"], delta=0.1)
        self.assertEqual(out["co2_ppm"], ENV["co2_ppm"])
        self.assertAlmostEqual(out["temp_c"], ENV["temp_c"], delta=0.06)

    def test_short_register_block_gives_nothing(self):
        raw = self.raw("air")
        raw["registers"] = raw["registers"][:3]
        self.assertEqual(self.a.translate("air_sensor", raw), {})

    def test_scanner_only_scans_the_local_site(self):
        self.assertEqual(parse_targets(["127.0.0.1:5020-5022"]),
                         [("127.0.0.1", 5020), ("127.0.0.1", 5021), ("127.0.0.1", 5022)])
        self.assertEqual(len(parse_targets(["192.168.4.0/29:502"])), 6)
        with self.assertRaisesRegex(ValueError, "not a local network"):
            parse_targets(["8.8.8.0/30:502"])
        with self.assertRaisesRegex(ValueError, "more than 256"):
            parse_targets(["10.0.0.0/16:502"])


if __name__ == "__main__":
    unittest.main()
