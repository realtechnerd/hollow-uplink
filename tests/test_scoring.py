"""Turning measurements into scores, recommendations and plain language (verifier/scoring.py).
These thresholds are what residents are told, so their edges are pinned down here."""

import unittest

from verifier import scoring


class Subscores(unittest.TestCase):
    def test_normal_conditions_score_full_marks(self):
        self.assertEqual(scoring.subscore("radiation_usv_h", 0.12), 100)
        self.assertEqual(scoring.subscore("air_quality_aqi", 40), 100)
        self.assertEqual(scoring.subscore("temp_c", 24), 100)
        self.assertEqual(scoring.subscore("gas_vs_clean_air", 1.1), 100)

    def test_documented_anchor_points(self):
        cases = [
            ("radiation_usv_h", 1, 80), ("radiation_usv_h", 5, 50), ("radiation_usv_h", 20, 20), ("radiation_usv_h", 100, 0),
            ("air_quality_aqi", 100, 80), ("air_quality_aqi", 200, 40), ("air_quality_aqi", 400, 0),
            ("temp_c", 22 + 18, 60), ("temp_c", 22 - 28, 20),
            ("gas_vs_clean_air", 2, 80), ("gas_vs_clean_air", 3, 50), ("gas_vs_clean_air", 4, 25), ("gas_vs_clean_air", 6, 0),
        ]
        for q, value, expected in cases:
            with self.subTest(q=q, value=value):
                self.assertAlmostEqual(scoring.subscore(q, value), expected, places=6)

    def test_worse_conditions_never_score_higher(self):
        for q, values in {
            "radiation_usv_h": [0.1, 0.5, 2, 8, 40, 200],
            "air_quality_aqi": [30, 80, 150, 250, 450],
            "gas_vs_clean_air": [1, 1.8, 2.5, 3.5, 5, 9],
        }.items():
            scores = [scoring.subscore(q, v) for v in values]
            self.assertEqual(scores, sorted(scores, reverse=True), q)

    def test_heat_and_cold_count_alike(self):
        self.assertAlmostEqual(scoring.subscore("temp_c", 22 + 25), scoring.subscore("temp_c", 22 - 25))

    def test_zero_or_negative_radiation_does_not_crash(self):
        self.assertEqual(scoring.subscore("radiation_usv_h", 0), 100)

    def test_unknown_quantities_dont_lower_the_score(self):
        self.assertEqual(scoring.subscore("wind_kph", 120), 100)


class Recommendations(unittest.TestCase):
    def test_bands(self):
        for score, rec in [(100, "outposts"), (80, "outposts"), (79.9, "domes"), (55, "domes"),
                           (54, "suits"), (30, "suits"), (29, "not_safe"), (0, "not_safe")]:
            self.assertEqual(scoring.recommendation(score), rec, score)


class PlainLanguage(unittest.TestCase):
    def test_describe(self):
        self.assertEqual(scoring.describe("radiation_usv_h", 0.12), "radiation normal (0.12 µSv/h)")
        self.assertEqual(scoring.describe("radiation_usv_h", 8), "radiation high (8.00 µSv/h)")
        self.assertEqual(scoring.describe("air_quality_aqi", 188), "air unhealthy (AQI 188)")
        self.assertEqual(scoring.describe("gas_vs_clean_air", 4.5), "gas or smoke dangerous (4.5× clean air)")

    def test_verdict_names_the_main_concern_and_ignored_sensors(self):
        measured = {"radiation_usv_h": 0.12, "air_quality_aqi": 188, "temp_c": 25}
        text = scoring.verdict(measured, "suits", "air_quality_aqi", flagged_count=2, measured_pct=60)
        self.assertTrue(text.startswith("Radiation normal"))
        self.assertIn("Short trips in full suits only.", text)
        self.assertIn("Main concern: air quality.", text)
        self.assertIn("Based on 60% of the zone.", text)
        self.assertIn("Ignoring 2 faulty sensors.", text)

    def test_verdict_with_nothing_measured(self):
        self.assertTrue(scoring.verdict({}, "unknown", None, 0).startswith("No machine has measured"))


if __name__ == "__main__":
    unittest.main()
