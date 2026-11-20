"""The simulated surface (simulators/world.py) and scout planning (simulators/scout.py).

Only simulators may see the world; these tests check it stays believable, and that the demo's
one piece of staging (scouts keeping out of an area) behaves."""

import math
import random
import unittest

from hollow import geo
from simulators import world as W
from simulators.scout import path_to, plan_path

RNG = random.Random(9)


def env(x, y, t=0):
    return W.environment(x, y, t, RNG)


class Environment(unittest.TestCase):
    def test_every_value_is_physically_plausible_everywhere(self):
        for x in range(5, geo.MAP_W, 45):
            for y in range(5, geo.MAP_H, 45):
                e = env(x, y, t=x * 3)
                with self.subTest(x=x, y=y):
                    self.assertGreater(e["radiation_usv_h"], 0)
                    self.assertGreaterEqual(e["aqi"], 0)
                    self.assertGreaterEqual(e["pm25_ugm3"], 0)
                    self.assertGreaterEqual(e["co2_ppm"], 380)
                    self.assertTrue(0 <= e["humidity_pct"] <= 100)
                    self.assertGreaterEqual(e["wind_kph"], 0)
                    self.assertTrue(10 < e["temp_c"] < 40)

    def test_the_hidden_hazards_are_where_the_story_says(self):
        self.assertGreater(env(640, 110)["radiation_usv_h"], 2)        # old hospital, Z3
        self.assertLess(env(100, 60)["radiation_usv_h"], 0.3)
        self.assertGreater(env(470, 420)["aqi"], 150)                    # landfill fire, Z5
        self.assertGreater(env(470, 420)["pm25_ugm3"], 35)
        self.assertGreater(env(225, 330)["co2_ppm"], 2000)               # collapsed metro vent, Z4
        self.assertLess(env(700, 60)["co2_ppm"], 500)

    def test_damp_by_the_river_and_sheltered_downtown(self):
        on_river = env(300, W.river_y(300))["humidity_pct"]
        far = env(300, 40)["humidity_pct"]
        self.assertGreater(on_river, far + 20)
        downtown = sum(env(*W.DOWNTOWN, t=t)["wind_kph"] for t in range(0, 400, 40))
        edge = sum(env(780, 20, t=t)["wind_kph"] for t in range(0, 400, 40))
        self.assertLess(downtown, edge / 2)


class World(unittest.TestCase):
    def test_same_seed_same_city(self):
        self.assertEqual(W.World(7).code, W.World(7).code)
        self.assertNotEqual(W.World(7).code, W.World(8).code)

    def test_buildings_block_line_of_sight(self):
        w = W.World()
        for gy in range(w.h):
            for gx in range(1, w.w - 1):
                if w.code[gy][gx] == W.BUILDING and w.passable(gx - 1, gy) and w.passable(gx + 1, gy):
                    self.assertFalse(w.visible(gx - 1, gy, gx + 1, gy))
                    return
        self.skipTest("no building with open ground on both sides")


class ScoutKeepAway(unittest.TestCase):
    """While the demo investigates the hijacked probe, untasked scouts stay out of a circle around it."""

    def setUp(self):
        self.w = W.World()
        self.known = {(gx, gy) for gy in range(self.w.h) for gx in range(self.w.w)}   # everything mapped: patrol mode
        self.centre, self.r = (330, 80), 70

    def keep_out(self, cell):
        cx, cy = (cell[0] + 0.5) * geo.CELL, (cell[1] + 0.5) * geo.CELL
        return math.hypot(cx - self.centre[0], cy - self.centre[1]) <= self.r

    def test_an_untasked_scout_never_drives_into_the_area(self):
        start = self.w.nearest_passable(60, 10)
        rng = random.Random(1)
        for _ in range(40):
            goal, path = plan_path(self.w, start, self.known, set(), rng, self.keep_out)
            self.assertFalse(self.keep_out(goal))
            self.assertFalse(any(self.keep_out(c) for c in path))

    def test_a_scout_caught_inside_drives_out(self):
        start = self.w.nearest_passable(33, 8)
        self.assertTrue(self.keep_out(start))
        goal, path = plan_path(self.w, start, self.known, set(), random.Random(2), self.keep_out)
        self.assertFalse(self.keep_out(goal))
        self.assertTrue(path)

    def test_the_scout_the_operator_sends_still_gets_there(self):
        start = self.w.nearest_passable(60, 10)
        goal = (self.centre[0] // geo.CELL, self.centre[1] // geo.CELL)
        reached, path = path_to(self.w, start, goal, self.known)
        self.assertLessEqual(math.dist(reached, goal), 2)
        self.assertTrue(any(self.keep_out(c) for c in path))

    def test_without_an_area_scouts_go_anywhere(self):
        start = self.w.nearest_passable(33, 8)
        goal, _ = plan_path(self.w, start, self.known, set(), random.Random(3))
        self.assertTrue(self.w.passable(*goal))


if __name__ == "__main__":
    unittest.main()
