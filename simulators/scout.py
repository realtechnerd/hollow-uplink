"""Simulated scout robots that explore the hidden surface with LiDAR.

They use frontier exploration (a standard robotics technique): each scout drives
to the nearest reachable spot on the edge of what the team has already seen,
scanning as it goes. Once everything reachable is mapped they patrol, so the map
stays fresh. Raw messages go to surface/raw/<gateway_id>/mqtt/<unit_id>.

    python -m simulators.scout                       # 2 scouts
    python -m simulators.scout --scouts 3 --tick 0.3
"""

import argparse
import json
import math
import queue
import random
import time
from collections import deque

from hollow import config, geo, topics
from hollow.mqtt import connect, make_client

from .world import World, environment

SCAN_RADIUS = 6


class Scout:
    def __init__(self, unit_id: str, gx: int, gy: int):
        self.unit_id = unit_id
        self.gx, self.gy = gx, gy
        self.theta = 0.0
        self.battery = 100.0
        self.path: list[tuple[int, int]] = []
        self.target: tuple[int, int] | None = None
        self.task: str | None = None       # ref of the task being carried out
        self.task_state = "idle"           # en_route | arrived
        self.linger = 0                    # ticks to stay put (on arrival or after a halt)


def path_to(world: World, start, goal, known: set):
    """BFS through known, passable cells to goal, or to the reachable cell closest to it."""
    parents = {start: None}
    queue = deque([start])
    best = start
    while queue:
        cell = queue.popleft()
        if cell == goal:
            best = cell
            break
        if math.dist(cell, goal) < math.dist(best, goal):
            best = cell
        x, y = cell
        for nxt in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if nxt not in parents and nxt in known and world.passable(*nxt):
                parents[nxt] = cell
                queue.append(nxt)
    return best, _unwind(parents, best)


def plan_path(world: World, start, known: set, avoid: set, rng: random.Random):
    """BFS to the nearest frontier cell (passable, next to unseen) not near another scout's target."""
    parents = {start: None}
    queue = deque([start])
    fallback = None
    while queue:
        cell = queue.popleft()
        x, y = cell
        is_frontier = any(
            world.in_bounds(nx, ny) and (nx, ny) not in known
            for nx, ny in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
        )
        if is_frontier and cell != start:
            if all(abs(x - ax) + abs(y - ay) > 8 for ax, ay in avoid):
                return cell, _unwind(parents, cell)
            fallback = fallback or cell
        for nxt in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if nxt not in parents and nxt in known and world.passable(*nxt):
                parents[nxt] = cell
                queue.append(nxt)
    if fallback:
        return fallback, _unwind(parents, fallback)
    # Everything reachable is mapped: patrol to a random known spot to keep data fresh.
    reachable = list(parents)
    goal = rng.choice(reachable)
    return goal, _unwind(parents, goal)


def _unwind(parents, cell):
    path = []
    while parents[cell] is not None:
        path.append(cell)
        cell = parents[cell]
    return list(reversed(path))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gateway", default=config.GATEWAY_ID)
    parser.add_argument("--scouts", type=int, default=2)
    parser.add_argument("--tick", type=float, default=0.5, help="seconds per move")
    parser.add_argument("--seed", type=int, default=2087, help="hidden world seed")
    parser.add_argument("--prefix", default="scout", help="unit id prefix, unique per teammate")
    args = parser.parse_args()

    world = World(args.seed)
    rng = random.Random(args.seed + 1)
    # Scouts emerge near the old maintenance shaft in the middle of the city.
    starts = [(38, 22), (44, 26), (34, 28), (46, 18)]
    scouts = []
    for i in range(args.scouts):
        gx, gy = world.nearest_passable(*starts[i % len(starts)])
        scouts.append(Scout(f"{args.prefix}-{i + 1:02d}", gx, gy))

    known: set[tuple[int, int]] = set()
    started = time.time()
    orders: queue.Queue = queue.Queue()   # vendor commands arriving from the gateway

    def on_command(client, userdata, msg):
        try:
            orders.put((msg.topic.rsplit("/", 1)[-1], json.loads(msg.payload)))
        except json.JSONDecodeError:
            pass

    client = make_client(f"scouts-{args.gateway}")
    client.on_connect = lambda c, u, f, rc, p: c.subscribe(topics.machine_command(args.gateway, "mqtt", "+"))
    client.on_message = on_command
    connect(client)
    client.loop_start()
    print(f"{len(scouts)} scouts exploring for {args.gateway} on {config.MQTT_HOST}:{config.MQTT_PORT}")

    by_id = {s.unit_id: s for s in scouts}
    try:
        while True:
            while not orders.empty():
                unit_id, order = orders.get()
                scout = by_id.get(unit_id)
                if not scout:
                    continue
                if order.get("op") == "goto":
                    goal = (int(order["x"] // geo.CELL), int(order["y"] // geo.CELL))
                    scout.target, scout.path = path_to(world, (scout.gx, scout.gy), goal, known)
                    scout.task, scout.task_state, scout.linger = order.get("ref"), "en_route", 0
                    print(f"{unit_id}: tasked {scout.task}, heading to {goal} ({len(scout.path)} steps)")
                elif order.get("op") == "halt":
                    scout.path, scout.task, scout.task_state, scout.linger = [], None, "idle", 20
                    print(f"{unit_id}: halted")

            for scout in scouts:
                hits = world.scan(scout.gx, scout.gy, SCAN_RADIUS)
                known.update((h[0], h[1]) for h in hits)

                if scout.task and scout.task_state == "en_route" and not scout.path:
                    scout.task_state, scout.linger = "arrived", 8   # stay and measure for a few ticks
                if scout.linger > 0:
                    scout.linger -= 1
                    if scout.linger == 0 and scout.task_state == "arrived":
                        scout.task, scout.task_state = None, "idle"
                elif not scout.path:
                    others = {s.target for s in scouts if s is not scout and s.target}
                    scout.target, scout.path = plan_path(world, (scout.gx, scout.gy), known, others, rng)
                if scout.path and scout.linger == 0:
                    nx, ny = scout.path.pop(0)
                    if world.passable(nx, ny):
                        scout.theta = math.degrees(math.atan2(ny - scout.gy, nx - scout.gx))
                        scout.gx, scout.gy = nx, ny
                    else:
                        scout.path = []
                scout.battery = max(20.0, scout.battery - 0.02)

                x = scout.gx * geo.CELL + geo.CELL / 2
                y = scout.gy * geo.CELL + geo.CELL / 2
                env = environment(x, y, time.time() - started, rng)
                raw = {
                    "model": "SCOUT-MK3",
                    "pose": {"x": x, "y": y, "theta": round(scout.theta, 1)},
                    "battery": round(scout.battery, 1),
                    "scan": {"cell": geo.CELL, "hits": hits},
                    # Onboard environmental payload (inspection robots commonly carry one)
                    "env": {"rad": env["radiation_usv_h"], "aqi": env["aqi"], "temp": env["temp_c"]},
                }
                if scout.task:
                    raw["task"] = {"ref": scout.task, "state": scout.task_state}
                client.publish(topics.raw(args.gateway, "mqtt", scout.unit_id), json.dumps(raw))
            time.sleep(args.tick)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()


if __name__ == "__main__":
    main()
