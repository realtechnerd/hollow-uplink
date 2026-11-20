"""The hidden "true" surface that simulated machines live in.

Only simulators may import this. The gateway and dashboard never see it: they
learn the surface solely from what scouts report. That keeps the demo honest:
the mapping pipeline is real, only the world it scans is simulated.

Grid is geo.GRID_W x geo.GRID_H cells; codes match the scout vendor format.
"""

import math
import random
from collections import deque

from hollow import geo

OPEN, RUBBLE, BUILDING, DAMAGED, VEGETATION, WATER = range(6)

# Hidden environmental hazards, in map units: (x, y, peak, radius)
RADIATION_HOTSPOTS = [
    (640, 110, 3.2, 55),    # old hospital imaging wing, Z3
    (130, 400, 0.9, 90),    # rail yard with leaking cargo, Z4
]
SMOKE_SOURCES = [
    (470, 420, 140, 75),    # landfill that has smouldered for decades, Z5
]
CO2_SOURCES = [
    (470, 420, 900, 60),    # the same landfill fire
    (225, 330, 2400, 40),   # collapsed metro vent: CO2 pools in the low ground, Z4
]
DOWNTOWN = (360, 200)       # tall buildings shelter the old centre from the wind


def river_y(x: float) -> float:
    """Centre line of the river, in map units (matches World._generate)."""
    return (35 + 4.5 * math.sin((x / 10) / 9.0)) * 10 + 5


def environment(x: float, y: float, t: float, rng: random.Random) -> dict:
    """True conditions at map point (x, y) at time t, with a little sensor noise."""
    def bump(cx, cy, peak, radius):
        return peak * math.exp(-((x - cx) ** 2 + (y - cy) ** 2) / radius ** 2)

    rad = 0.12 + sum(bump(*h) for h in RADIATION_HOTSPOTS)
    smoke = sum(bump(*s) for s in SMOKE_SOURCES)
    aqi = 40 + 8 * math.sin(t / 120 + x / 200) + smoke
    temp = 26 + 3 * math.sin(t / 300) - 2 * math.exp(-((y - 350) / 40) ** 2)  # cooler by the river
    # Fine dust: settled ash everywhere, much worse downwind of the landfill smoke.
    pm25 = 5 + 2 * math.sin(t / 150 + y / 120) + 0.36 * smoke
    co2 = 425 + sum(bump(*c) for c in CO2_SOURCES)
    river = math.exp(-((y - river_y(x)) / 45) ** 2)
    humidity = 40 + 38 * river + 5 * math.sin(t / 200 + x / 300)
    sheltered = math.exp(-math.dist((x, y), DOWNTOWN) ** 2 / 170 ** 2)
    wind = 9 + 26 * (1 - sheltered) + 8 * river + 4 * math.sin(t / 40 + x / 90)
    return {
        "radiation_usv_h": round(rad * rng.uniform(0.93, 1.07), 3),
        "aqi": int(max(0, aqi + rng.uniform(-4, 4))),
        "temp_c": round(temp + rng.uniform(-0.3, 0.3), 1),
        "pm25_ugm3": round(max(0.0, pm25 * rng.uniform(0.9, 1.1)), 1),
        "co2_ppm": int(co2 * rng.uniform(0.97, 1.03)),
        "humidity_pct": int(min(100, max(0, humidity + rng.uniform(-3, 3)))),
        "wind_kph": round(max(0.0, wind * rng.uniform(0.85, 1.15)), 1),
    }


PASSABLE = {OPEN, RUBBLE, VEGETATION}
BLOCKS_SIGHT = {BUILDING, DAMAGED}
BLOCK = 8  # a street every 8 cells


class World:
    def __init__(self, seed: int = 2087):
        self.w, self.h = geo.GRID_W, geo.GRID_H
        self.code = [[OPEN] * self.w for _ in range(self.h)]
        self.height = [[0.0] * self.w for _ in range(self.h)]
        self._generate(random.Random(seed))

    # ---------- generation ----------
    def _set(self, gx: int, gy: int, code: int, height: float) -> None:
        if 0 <= gx < self.w and 0 <= gy < self.h:
            self.code[gy][gx] = code
            self.height[gy][gx] = height

    @staticmethod
    def is_street(gx: int, gy: int) -> bool:
        return gx % BLOCK == 0 or gy % BLOCK == 0

    def _generate(self, rng: random.Random) -> None:
        # City blocks: buildings get taller toward the old downtown.
        for by in range(0, self.h, BLOCK):
            for bx in range(0, self.w, BLOCK):
                for gy in range(by + 1, min(by + BLOCK, self.h)):
                    for gx in range(bx + 1, min(bx + BLOCK, self.w)):
                        if rng.random() < 0.18:
                            self._set(gx, gy, RUBBLE, rng.uniform(0.8, 3.0))
                cx, cy = bx + BLOCK / 2, by + BLOCK / 2
                dist = math.hypot((cx - self.w * 0.45) / self.w, (cy - self.h * 0.4) / self.h)
                downtown = max(0.0, 1.0 - dist * 2.4)
                for _ in range(rng.randint(2, 5)):
                    fw, fh = rng.randint(2, 4), rng.randint(2, 4)
                    x0 = rng.randint(bx + 1, bx + BLOCK - fw)
                    y0 = rng.randint(by + 1, by + BLOCK - fh)
                    tall = rng.uniform(8, 22) + downtown * rng.uniform(15, 70)
                    roll = rng.random()
                    if roll < 0.15:
                        code, tall = RUBBLE, rng.uniform(1.5, 4.5)       # collapsed
                    elif roll < 0.42:
                        code, tall = DAMAGED, tall * rng.uniform(0.5, 0.8)
                    else:
                        code = BUILDING
                    for gy in range(y0, y0 + fh):
                        for gx in range(x0, x0 + fw):
                            self._set(gx, gy, code, round(tall, 1))

        # Streets: mostly clear, some debris.
        for gy in range(self.h):
            for gx in range(self.w):
                if self.is_street(gx, gy):
                    if rng.random() < 0.06:
                        self._set(gx, gy, RUBBLE, rng.uniform(0.5, 2.0))
                    else:
                        self._set(gx, gy, OPEN, 0.0)

        # Thirty years of nobody: vegetation reclaiming parks and streets.
        for _ in range(16):
            cx, cy = rng.uniform(0, self.w), rng.uniform(0, self.h)
            radius = rng.uniform(2.5, 6.5)
            for gy in range(int(cy - radius), int(cy + radius) + 1):
                for gx in range(int(cx - radius), int(cx + radius) + 1):
                    if not (0 <= gx < self.w and 0 <= gy < self.h):
                        continue
                    if math.hypot(gx - cx, gy - cy) > radius or rng.random() > 0.8:
                        continue
                    if self.code[gy][gx] in (OPEN, RUBBLE):
                        self._set(gx, gy, VEGETATION, rng.uniform(3.0, 14.0))

        # A river through the south of the city; north-south streets cross it as bridges.
        for gx in range(self.w):
            centre = 35 + 4.5 * math.sin(gx / 9.0)
            for gy in range(self.h):
                if abs(gy - centre) < 1.7 and gx % BLOCK != 0:
                    self._set(gx, gy, WATER, 0.0)

    # ---------- queries used by simulated machines ----------
    def in_bounds(self, gx: int, gy: int) -> bool:
        return 0 <= gx < self.w and 0 <= gy < self.h

    def passable(self, gx: int, gy: int) -> bool:
        return self.in_bounds(gx, gy) and self.code[gy][gx] in PASSABLE

    def nearest_passable(self, gx: int, gy: int) -> tuple[int, int]:
        seen, queue = {(gx, gy)}, deque([(gx, gy)])
        while queue:
            x, y = queue.popleft()
            if self.passable(x, y):
                return x, y
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
                if self.in_bounds(nx, ny) and (nx, ny) not in seen:
                    seen.add((nx, ny))
                    queue.append((nx, ny))
        raise ValueError("no passable cell")

    def visible(self, ox: int, oy: int, tx: int, ty: int) -> bool:
        """Line of sight on the grid: buildings hide what's behind them."""
        steps = max(abs(tx - ox), abs(ty - oy))
        for i in range(1, steps):
            x = round(ox + (tx - ox) * i / steps)
            y = round(oy + (ty - oy) * i / steps)
            if (x, y) == (tx, ty):
                break
            if self.code[y][x] in BLOCKS_SIGHT:
                return False
        return True

    def scan(self, ox: int, oy: int, radius: int) -> list[list]:
        """Simulated LiDAR: every visible cell within radius as [gx, gy, height_m, code]."""
        hits = []
        for gy in range(oy - radius, oy + radius + 1):
            for gx in range(ox - radius, ox + radius + 1):
                if not self.in_bounds(gx, gy):
                    continue
                if (gx - ox) ** 2 + (gy - oy) ** 2 > radius * radius:
                    continue
                if self.visible(ox, oy, gx, gy):
                    hits.append([gx, gy, self.height[gy][gx], self.code[gy][gx]])
        return hits
