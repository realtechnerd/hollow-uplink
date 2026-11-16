# Sprite generation prompt — Hollow Uplink

Paste everything below the line into the image AI. If it can only make one image per request, send the "Style" and "Rules" sections each time, followed by one asset from the list.

---

You are creating a matching set of game-style sprites for **Hollow Uplink**, a software project shown as a live 3D map of an abandoned city's surface.

## The world

It is 2117. In 2087 superstorms, fires and poisoned air drove humanity underground into "the Hollow", a network of cities dug into old mines and subway tunnels. Nobody has been to the surface in thirty years. Up there, the machines humanity left behind are still running: sensor masts, radiation probes, weather stations, power stations, and robots. Hollow Uplink finds those machines, listens to their sensors, and maps the surface from what they report, to find out whether people can go home.

The mood: quiet, eerie, hopeful. The city is ruined and overgrown, not on fire. Nature is reclaiming it. The machines are old, weathered and patched, but still working, and they are the heroes.

## Style

- Clean low-poly 3D look rendered as 2D, like a modern minimalist strategy game. Flat-shaded faces, simple chunky shapes, no photorealism, no painterly texture, no outlines.
- Camera: three-quarter top-down view (isometric-style, about 35° down), lit from the upper left. Use the same angle and light for every sprite.
- Machines are built from 1990s–2040s industrial hardware: painted steel, hazard-yellow panels, worn decals, small status lights.
- Palette (stick to it):
  - Ground and concrete: #2A2C26, #4A4B44, #7C807D
  - Rust and damage: #8E6F4C, #6B6052
  - Overgrowth: #4F7D3A, #6F9A4A
  - Water: #2C5A74
  - Machine hazard yellow: #C9A227
  - Status lights: green #4CC38A (verified), red #FF5A5F (rejected), amber #F5A524 (warning), cyan #8FD3FF (scanning)
- Every machine has one small glowing status light on top, shown green. Also make a red version of each machine sprite.

## Rules

- Transparent background (PNG with alpha) for every sprite except backgrounds and title art.
- Centered subject with about 10% padding, nothing cut off at the edges.
- No text, letters, numbers, logos or watermarks in any sprite.
- Keep proportions consistent: a scout robot is about one third the height of a sensor mast.
- No people anywhere on the surface; the surface is empty of humans.
- Readable at small sizes: strong silhouette, no tiny detail that disappears at 64 px.

## Assets

### 1. Machine sprites (512 × 512, transparent; one green-light and one red-light version each)

1. **Air sensor:** a thin steel pole with a boxy sensor head and a small spinning intake fan on top, a cracked solar panel on one side.
2. **Radiation probe:** a squat cylindrical body on a weighted base, a small dish on top, a yellow-and-black trefoil hazard band around it.
3. **Weather mast:** a tall lattice mast with a crossbar, a three-cup anemometer and a wind vane at the top, vines climbing the bottom third.
4. **Power station:** a chunky industrial building block with two short exhaust stacks, pipes and a fenced transformer, moss on the roof.
5. **Scout robot:** a small four-wheeled rover in hazard yellow with a short mast and a spinning LiDAR puck on top, mud on the wheels. Also make three extra frames of it driving (wheel and puck rotation) for a simple animation.
6. **Builder robot:** a larger tracked robot with a small crane arm and a cargo bed holding prefab panels.
7. **Unknown machine:** a scratched grey metal box with a blinking light and a question-mark-shaped silhouette of cables, for machines the system hasn't identified yet.

### 2. Map icons (128 × 128, transparent, flat 2D, simplified versions of the machines above)

One icon per machine: air sensor, radiation probe, weather mast, power station, scout robot, builder robot, unknown machine. Same palette, same light. Each sits inside a soft dark circular badge.

### 3. Status badges (64 × 64, transparent, flat)

- Verified: a green shield with a check mark
- Rejected: a red shield with an X
- Warning: an amber triangle with an exclamation mark
- Scanning: cyan concentric arcs (radar sweep)
- Unmapped: a grey dashed circle

### 4. Terrain tiles (256 × 256, seamless, tileable top-down textures, no transparency)

1. Cracked asphalt with faded road markings
2. Rubble: broken concrete chunks and rebar
3. Overgrown ground: grass and weeds pushing through pavement
4. Dense vegetation: shrubs and young trees, seen from above
5. Still water with algae
6. Building rooftop: grey concrete roof with vents and puddles
7. Damaged rooftop: same as 6 but cracked, partly collapsed, rust stains

### 5. Zone overlay markers (256 × 256, transparent)

Map pins showing what a zone is ready for: a person in an environment suit (suits), a geodesic dome (domes), a small fortified building with a flag (outposts), and a red skull-free hazard sign (not safe: use a red octagon with a horizontal bar).

### 6. Video and title art (1920 × 1080, no transparency)

1. **Title card background:** a dark ruined city skyline at dusk under a hazy green-grey sky, vines on the towers, a few tiny green status lights glowing among the ruins. Leave the center third fairly empty so a title can be placed over it.
2. **The Hollow:** a vast underground city carved into a mine: stacked levels of cramped housing, dim lamps, pipes, crowds far away, a huge sealed steel shaft door at the top.
3. **The sealed shaft:** a close view of a massive round blast door in a rock ceiling, warning stripes, faint light leaking around the edges.
4. **First return:** dawn on the surface, a small scout robot on an overgrown street looking toward a geodesic dome being assembled in the distance.

Deliver every sprite as a separate file named after its asset, for example `machine_scout_robot_green.png`, `icon_radiation_probe.png`, `tile_rubble.png`.
