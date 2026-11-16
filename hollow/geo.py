"""Shared map geometry. Must match ZONES in dashboard/app.js and dashboard/city.js.

The surface map is 800 x 500 map units, split into a 3 x 2 grid of zones (Z1..Z6),
and into an 80 x 50 survey grid of 10-unit cells. One cell is 5 m on the ground.
"""

MAP_W = 800
MAP_H = 500
ZONE_COLS = 3
ZONE_ROWS = 2

CELL = 10            # map units per survey cell
CELL_M = 5           # metres per survey cell
GRID_W = MAP_W // CELL   # 80
GRID_H = MAP_H // CELL   # 50

# Survey cell classes, in the common format
SURFACE_CLASSES = ("open", "rubble", "building", "building_damaged", "vegetation", "water")


def zone_for(x: float, y: float) -> str:
    col = min(ZONE_COLS - 1, max(0, int(x // (MAP_W / ZONE_COLS))))
    row = min(ZONE_ROWS - 1, max(0, int(y // (MAP_H / ZONE_ROWS))))
    return f"Z{row * ZONE_COLS + col + 1}"
