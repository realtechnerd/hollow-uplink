// Hollow Uplink · 3D surface view.
//
// Nothing here is drawn from imagination. The surface starts dark; every tile
// on screen came from a verified scout survey, and fades as its last scan ages.
// Machines appear only when the gateway discovers them, and sensor readings are
// drawn as fields on the ground.
import * as THREE from "three";
import { OrbitControls } from "three/addons/controls/OrbitControls.js";
import { CSS2DRenderer, CSS2DObject } from "three/addons/renderers/CSS2DRenderer.js";

// ---------- geometry shared with hollow/geo.py ----------
const MAP_W = 800, MAP_H = 500, CELL = 10, CELL_M = 5;
const GW = MAP_W / CELL, GH = MAP_H / CELL;          // 80 x 50 survey grid
const ZONE_W = MAP_W / 3, ZONE_H = MAP_H / 2;
const ZONES = ["Z1", "Z2", "Z3", "Z4", "Z5", "Z6"].map((id, i) => ({
  id, mx: (i % 3) * ZONE_W, my: Math.floor(i / 3) * ZONE_H,
}));
const zoneIndexFor = (mx, my) =>
  Math.min(1, Math.floor(my / ZONE_H)) * 3 + Math.min(2, Math.floor(mx / ZONE_W));

// One world unit = one survey cell = 5 m. Map (x, y) -> world (x, z), centred.
const toWorld = (mx, my) => new THREE.Vector3(mx / CELL - GW / 2, 0, my / CELL - GH / 2);
const metresToWorld = (m) => m / CELL_M;

const FRESH_FOR_S = 90;           // a tile fades over this long without a rescan
const REJECT_HOLD_MS = 8000;      // a unit stays red this long after a rejected message

// How each surveyed class is drawn. Shapes stay one-per-measured-cell; the polish is
// in how cells join up and how surfaces are shaded, never in adding things nobody saw.
const CLASS_STYLE = {
  open:             { color: 0x46483f, minH: 0.04, maxH: 0.04, fp: 1.0 },
  rubble:           { color: 0x6e6355, minH: 0.12, maxH: 1.2,  fp: 0.78 },
  building:         { color: 0x8a8d86, minH: 0.6,  maxH: 30,   fp: 1.0 },
  building_damaged: { color: 0x9a7a58, minH: 0.4,  maxH: 30,   fp: 1.0 },
  vegetation:       { color: 0x37442a, minH: 0.05, maxH: 0.05, fp: 1.0 },  // ground; the tree is drawn separately
  water:            { color: 0x2c5f7e, minH: 0.02, maxH: 0.02, fp: 1.0 },
};
const STRUCTURE_INSET = 0.07;  // gap between separate buildings, in cells
const CLASS_NAMES = Object.keys(CLASS_STYLE);
const CLASS_LABEL = {
  open: "Open ground", rubble: "Rubble", building: "Building",
  building_damaged: "Damaged building", vegetation: "Vegetation", water: "Water",
};

// ---------- DOM ----------
const $ = (id) => document.getElementById(id);
const container = $("scene");
const tooltip = $("tooltip");

// ---------- renderer, scene, camera ----------
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
renderer.setSize(window.innerWidth, window.innerHeight);
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
container.appendChild(renderer.domElement);

const labelRenderer = new CSS2DRenderer();
labelRenderer.setSize(window.innerWidth, window.innerHeight);
Object.assign(labelRenderer.domElement.style, { position: "absolute", top: "0", left: "0", pointerEvents: "none" });
container.appendChild(labelRenderer.domElement);

const scene = new THREE.Scene();
const SKY = 0x16181a;
scene.background = new THREE.Color(SKY);
scene.fog = new THREE.Fog(SKY, 70, 150);

const camera = new THREE.PerspectiveCamera(42, window.innerWidth / window.innerHeight, 0.5, 400);
camera.position.set(0, 52, 56);

const controls = new OrbitControls(camera, renderer.domElement);
controls.enableDamping = true;
controls.maxPolarAngle = 1.32;
controls.minDistance = 10;
controls.maxDistance = 130;
controls.autoRotate = true;
controls.autoRotateSpeed = 0.35;
controls.addEventListener("start", () => {
  // Stop the idle orbit as soon as someone grabs the camera.
  if (controls.autoRotate) { controls.autoRotate = false; $("t-rotate").checked = false; }
});

scene.add(new THREE.HemisphereLight(0xb7c2b0, 0x1d1a14, 1.1));
const sun = new THREE.DirectionalLight(0xffd7a8, 2.4);
sun.position.set(-30, 60, 25);
sun.castShadow = true;
sun.shadow.mapSize.set(2048, 2048);
Object.assign(sun.shadow.camera, { left: -50, right: 50, top: 40, bottom: -40, near: 1, far: 160 });
sun.shadow.bias = -0.0005;
scene.add(sun);

// ---------- uncharted ground ----------
const ground = new THREE.Mesh(
  new THREE.PlaneGeometry(GW, GH),
  new THREE.MeshStandardMaterial({ color: 0x0e100d, roughness: 1 }),
);
ground.rotation.x = -Math.PI / 2;
ground.receiveShadow = true;
scene.add(ground);

const outside = new THREE.Mesh(
  new THREE.PlaneGeometry(400, 400),
  new THREE.MeshStandardMaterial({ color: 0x0a0b09, roughness: 1 }),
);
outside.rotation.x = -Math.PI / 2;
outside.position.y = -0.05;
scene.add(outside);

// A faint survey grid every 10 cells (50 m) so the dark reads as "uncharted", not "empty".
{
  const pts = [];
  for (let x = 0; x <= GW; x += 10) pts.push(x - GW / 2, 0.01, -GH / 2, x - GW / 2, 0.01, GH / 2);
  for (let z = 0; z <= GH; z += 10) pts.push(-GW / 2, 0.01, z - GH / 2, GW / 2, 0.01, z - GH / 2);
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(pts, 3));
  scene.add(new THREE.LineSegments(geo, new THREE.LineBasicMaterial({ color: 0x2b3530, transparent: true, opacity: 0.6 })));
}

// ---------- surveyed tiles (one instance per grid cell) ----------
const N = GW * GH;
const tileGeo = new THREE.BoxGeometry(1, 1, 1);
tileGeo.translate(0, 0.5, 0);
// Per-instance surface kind for the shader: 0 = ground/rubble/water, 1 = building, 2 = damaged building.
const kindAttr = new THREE.InstancedBufferAttribute(new Float32Array(N), 1);
tileGeo.setAttribute("aKind", kindAttr);

const tileMaterial = new THREE.MeshStandardMaterial({ roughness: 0.92, metalness: 0.04 });
// Facades: windows, grime and darker ground floors, computed from world position so
// merged buildings get one continuous pattern instead of stretched textures.
tileMaterial.onBeforeCompile = (shader) => {
  shader.vertexShader = shader.vertexShader
    .replace("#include <common>", `#include <common>
      attribute float aKind;
      varying float vKind;
      varying vec3 vWPos;
      varying vec3 vWNormal;`)
    .replace("#include <worldpos_vertex>", `#include <worldpos_vertex>
      vKind = aKind;
      vWPos = (modelMatrix * instanceMatrix * vec4(transformed, 1.0)).xyz;
      vWNormal = normalize(mat3(modelMatrix) * mat3(instanceMatrix) * objectNormal);`);
  shader.fragmentShader = shader.fragmentShader
    .replace("#include <common>", `#include <common>
      varying float vKind;
      varying vec3 vWPos;
      varying vec3 vWNormal;
      float hHash(vec2 p) { return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453); }`)
    .replace("#include <color_fragment>", `#include <color_fragment>
      if (vKind > 0.5) {
        bool damaged = vKind > 1.5;
        if (abs(vWNormal.y) < 0.5) {
          // Walls: a window grid of about 2 m bays and 3.5 m floors.
          float u = abs(vWNormal.x) > 0.5 ? vWPos.z : vWPos.x;
          float side = vWNormal.x * 13.0 + vWNormal.z * 7.0;
          vec2 f = vec2(fract(u * 2.5), fract(vWPos.y / 0.7));
          vec2 cellId = vec2(floor(u * 2.5) + side, floor(vWPos.y / 0.7));
          float win = step(0.2, f.x) * step(f.x, 0.8) * step(0.28, f.y) * step(f.y, 0.8) * step(0.35, vWPos.y);
          float r = hHash(cellId);
          float glass = r < 0.07 ? 0.8 : (r > (damaged ? 0.55 : 0.82) ? 0.14 : 0.36);  // reflective, broken, dark
          diffuseColor.rgb *= mix(1.0, glass, win);
          if (r < 0.07) diffuseColor.rgb += win * vec3(0.02, 0.035, 0.05);
          // Vertical grime streaks, heavier on damaged buildings.
          float streak = hHash(vec2(floor(u * 7.0) + side, 3.0));
          diffuseColor.rgb *= 1.0 - (damaged ? 0.22 : 0.1) * streak * (1.0 - win);
          // Darker toward the ground.
          diffuseColor.rgb *= mix(0.62, 1.0, smoothstep(0.0, 2.2, vWPos.y));
        } else if (vWNormal.y > 0.5) {
          // Roofs: patchy, slightly darker.
          diffuseColor.rgb *= 0.78 + 0.14 * hHash(floor(vWPos.xz * 2.0));
        }
      }`);
};

const tiles = new THREE.InstancedMesh(tileGeo, tileMaterial, N);
tiles.castShadow = true;
tiles.receiveShadow = true;
tiles.frustumCulled = false;
scene.add(tiles);

// Vegetation: one low-poly tree per surveyed vegetation cell, sized by its measured height.
const trunkGeo = new THREE.CylinderGeometry(0.05, 0.08, 1, 5);
trunkGeo.translate(0, 0.5, 0);
const trunks = new THREE.InstancedMesh(trunkGeo, new THREE.MeshStandardMaterial({ color: 0x4a3b2a, roughness: 1 }), N);
const canopies = new THREE.InstancedMesh(
  new THREE.IcosahedronGeometry(0.5, 0),
  new THREE.MeshStandardMaterial({ roughness: 0.9, flatShading: true }),
  N,
);
for (const m of [trunks, canopies]) {
  m.castShadow = true;
  m.receiveShadow = true;
  m.frustumCulled = false;
  scene.add(m);
}

const hash = (n) => {
  const x = Math.sin(n * 12.9898 + 78.233) * 43758.5453;
  return x - Math.floor(x);
};
function markMatrices() {
  tiles.instanceMatrix.needsUpdate = true;
  trunks.instanceMatrix.needsUpdate = true;
  canopies.instanceMatrix.needsUpdate = true;
  kindAttr.needsUpdate = true;
}
function markColours() {
  tiles.instanceColor.needsUpdate = true;
  canopies.instanceColor.needsUpdate = true;
}

const cellClass = new Int8Array(N).fill(-1);    // index into CLASS_NAMES, -1 = never seen
const cellHeightM = new Float32Array(N);
const cellSeen = new Float64Array(N);           // performance.now() of last scan
const cellScanner = new Array(N);               // unit id that last scanned it
const cellZone = new Uint8Array(N);
const zoneTotals = new Array(6).fill(0);
const zoneKnown = new Array(6).fill(0);
let knownCount = 0;
const revealing = new Map();                    // index -> reveal start time

const dummy = new THREE.Object3D();
const tmpColor = new THREE.Color();
const hiddenMatrix = new THREE.Matrix4().makeTranslation(0, -200, 0);

for (let i = 0; i < N; i++) {
  tiles.setMatrixAt(i, hiddenMatrix);
  tiles.setColorAt(i, tmpColor.set(0x000000));
  trunks.setMatrixAt(i, hiddenMatrix);
  canopies.setMatrixAt(i, hiddenMatrix);
  canopies.setColorAt(i, tmpColor.set(0x000000));
  const gx = i % GW, gy = Math.floor(i / GW);
  cellZone[i] = zoneIndexFor(gx * CELL + CELL / 2, gy * CELL + CELL / 2);
  zoneTotals[cellZone[i]]++;
}

function tileHeight(i) {
  const s = CLASS_STYLE[CLASS_NAMES[cellClass[i]]];
  return THREE.MathUtils.clamp(metresToWorld(cellHeightM[i]), s.minH, s.maxH);
}

const IS_STRUCTURE = new Set([CLASS_NAMES.indexOf("building"), CLASS_NAMES.indexOf("building_damaged")]);
const VEGETATION = CLASS_NAMES.indexOf("vegetation");
const RUBBLE = CLASS_NAMES.indexOf("rubble");
const DAMAGED = CLASS_NAMES.indexOf("building_damaged");

// Two cells belong to the same building when scouts measured the same class and height.
const sameStructure = (i, j) => cellClass[j] === cellClass[i] && cellHeightM[j] === cellHeightM[i];

function placeTile(i, grow = 1) {
  const ci = cellClass[i];
  const s = CLASS_STYLE[CLASS_NAMES[ci]];
  const gx = i % GW, gy = Math.floor(i / GW);
  let x0 = gx, x1 = gx + 1, z0 = gy, z1 = gy + 1;
  if (IS_STRUCTURE.has(ci)) {
    // Extend into neighbouring cells of the same building; leave a gap everywhere else.
    if (!(gx > 0 && sameStructure(i, i - 1))) x0 += STRUCTURE_INSET;
    if (!(gx < GW - 1 && sameStructure(i, i + 1))) x1 -= STRUCTURE_INSET;
    if (!(gy > 0 && sameStructure(i, i - GW))) z0 += STRUCTURE_INSET;
    if (!(gy < GH - 1 && sameStructure(i, i + GW))) z1 -= STRUCTURE_INSET;
  } else {
    const pad = (1 - s.fp) / 2;
    x0 += pad; x1 -= pad; z0 += pad; z1 -= pad;
  }
  let h = tileHeight(i);
  // Damaged buildings get a broken roofline (±10% around the measured height).
  if (ci === DAMAGED) h *= 0.9 + 0.2 * hash(i);
  dummy.position.set((x0 + x1) / 2 - GW / 2, 0, (z0 + z1) / 2 - GH / 2);
  dummy.rotation.set(0, 0, 0);
  if (ci === RUBBLE) dummy.rotation.set((hash(i + 7) - 0.5) * 0.35, hash(i + 3) * Math.PI, (hash(i + 11) - 0.5) * 0.35);
  dummy.scale.set(x1 - x0, Math.max(0.001, h * grow), z1 - z0);
  dummy.updateMatrix();
  tiles.setMatrixAt(i, dummy.matrix);
  kindAttr.setX(i, ci === DAMAGED ? 2 : IS_STRUCTURE.has(ci) ? 1 : 0);
  placeTree(i, grow);
}

function placeTree(i, grow) {
  if (cellClass[i] !== VEGETATION) {
    trunks.setMatrixAt(i, hiddenMatrix);
    canopies.setMatrixAt(i, hiddenMatrix);
    return;
  }
  const gx = i % GW, gy = Math.floor(i / GW);
  const H = THREE.MathUtils.clamp(metresToWorld(cellHeightM[i]), 0.6, 2.8) * Math.max(grow, 0.001);
  const cx = gx + 0.5 - GW / 2 + (hash(i * 3 + 1) - 0.5) * 0.3;
  const cz = gy + 0.5 - GH / 2 + (hash(i * 3 + 2) - 0.5) * 0.3;
  dummy.rotation.set(0, 0, 0);
  dummy.position.set(cx, 0, cz);
  dummy.scale.set(1, H * 0.45, 1);
  dummy.updateMatrix();
  trunks.setMatrixAt(i, dummy.matrix);
  const r = Math.min(0.62, 0.3 + H * 0.16);
  dummy.position.set(cx, H * 0.45 + r * 0.7, cz);
  dummy.rotation.set(0, hash(i + 5) * Math.PI, 0);
  dummy.scale.set(r * 2, r * 2.3, r * 2);
  dummy.updateMatrix();
  canopies.setMatrixAt(i, dummy.matrix);
}

// Re-place the 4 neighbours of a changed cell, since buildings join across cells.
function replaceNeighbours(cells) {
  const done = new Set();
  for (const i of cells) {
    const gx = i % GW;
    for (const j of [gx > 0 ? i - 1 : -1, gx < GW - 1 ? i + 1 : -1, i - GW, i + GW]) {
      if (j < 0 || j >= N || done.has(j) || cellClass[j] === -1 || revealing.has(j)) continue;
      done.add(j);
      placeTile(j);
    }
  }
}

function colourTile(i, now) {
  const ci = cellClass[i];
  const s = CLASS_STYLE[CLASS_NAMES[ci]];
  const fresh = 0.38 + 0.62 * THREE.MathUtils.clamp(1 - (now - cellSeen[i]) / 1000 / FRESH_FOR_S, 0, 1);
  const vary = 0.9 + 0.14 * hash(i + 17);  // slight per-cell variation so ground doesn't look flat
  tiles.setColorAt(i, tmpColor.set(s.color).multiplyScalar(fresh * vary));
  if (ci === VEGETATION) {
    tmpColor.setHSL(0.26 + (hash(i) - 0.5) * 0.07, 0.42, 0.26 + hash(i + 5) * 0.08);
    canopies.setColorAt(i, tmpColor.multiplyScalar(fresh));
  }
}

function applySurvey(survey, scannerId, now) {
  if (!survey || survey.cell_size !== CELL || !Array.isArray(survey.cells)) return;
  const touched = [];
  for (const cell of survey.cells) {
    const [gx, gy, h, cls] = cell;
    const ci = CLASS_NAMES.indexOf(cls);
    if (ci < 0 || gx < 0 || gy < 0 || gx >= GW || gy >= GH) continue;
    const i = gy * GW + gx;
    const isNew = cellClass[i] === -1;
    const changed = cellClass[i] !== ci || cellHeightM[i] !== h;
    cellClass[i] = ci;
    cellHeightM[i] = h;
    cellSeen[i] = now;
    cellScanner[i] = scannerId;
    if (isNew) {
      knownCount++;
      zoneKnown[cellZone[i]]++;
      revealing.set(i, now);
      touched.push(i);
    } else if (changed) {
      placeTile(i);
      touched.push(i);
    }
    colourTile(i, now);
  }
  replaceNeighbours(touched);
  markMatrices();
  markColours();
}

// The verifier's retained snapshot: everything mapped so far, with each cell's age.
// Only fills cells we haven't seen more recently ourselves.
function applySnapshot(snap, now) {
  if (snap.cell_size !== CELL || !Array.isArray(snap.cells)) return;
  let added = 0;
  const touched = [];
  for (const [gx, gy, h, cls, ageS, scanner] of snap.cells) {
    const ci = CLASS_NAMES.indexOf(cls);
    if (ci < 0 || gx < 0 || gy < 0 || gx >= GW || gy >= GH) continue;
    const i = gy * GW + gx;
    const seen = now - ageS * 1000;
    if (cellClass[i] !== -1 && cellSeen[i] >= seen) continue;
    if (cellClass[i] === -1) { knownCount++; zoneKnown[cellZone[i]]++; revealing.set(i, now + Math.random() * 800); added++; }
    cellClass[i] = ci;
    cellHeightM[i] = h;
    cellSeen[i] = seen;
    cellScanner[i] = scanner;
    if (!revealing.has(i)) placeTile(i);
    colourTile(i, now);
    touched.push(i);
  }
  replaceNeighbours(touched);
  markMatrices();
  markColours();
  if (added) ticker(`Loaded ${added} previously surveyed cells from the verifier's map`, "info");
}

function surfaceTopAt(mx, my) {
  const gx = Math.floor(mx / CELL), gy = Math.floor(my / CELL);
  if (gx < 0 || gy < 0 || gx >= GW || gy >= GH) return 0;
  const i = gy * GW + gx;
  return cellClass[i] === -1 ? 0 : tileHeight(i);
}

// ---------- zones ----------
const zoneObjs = ZONES.map((z) => {
  const centre = toWorld(z.mx + ZONE_W / 2, z.my + ZONE_H / 2);
  const w = ZONE_W / CELL, d = ZONE_H / CELL;

  const outline = new THREE.LineSegments(
    new THREE.EdgesGeometry(new THREE.PlaneGeometry(w - 0.3, d - 0.3)),
    new THREE.LineBasicMaterial({ color: 0x8fd3ff, transparent: true, opacity: 0.22 }),
  );
  outline.rotation.x = -Math.PI / 2;
  outline.position.set(centre.x, 0.06, centre.z);
  scene.add(outline);

  const tint = new THREE.Mesh(
    new THREE.PlaneGeometry(w - 0.3, d - 0.3),
    new THREE.MeshBasicMaterial({ color: 0x4cc38a, transparent: true, opacity: 0.0, depthWrite: false }),
  );
  tint.rotation.x = -Math.PI / 2;
  tint.position.set(centre.x, 0.07, centre.z);
  scene.add(tint);

  const el = document.createElement("div");
  el.innerHTML = `<div class="zone-label">${z.id}</div><div class="zone-score">0% mapped · no score yet</div>`;
  const label = new CSS2DObject(el);
  label.position.copy(toWorld(z.mx + 24, z.my + 26));
  scene.add(label);

  return { ...z, outline, tint, label, scoreEl: el.querySelector(".zone-score"), score: null };
});

const RECOMMENDATION_COLOUR = { not_safe: 0xff5a5f, suits: 0xf5a524, domes: 0x8fd3ff, outposts: 0x4cc38a, unknown: 0x6b7068 };
const REC_LABEL = { outposts: "Outposts", domes: "Domes", suits: "Suits only", not_safe: "Unsafe", unknown: "Unknown" };

function renderZoneLabels() {
  zoneObjs.forEach((z, idx) => {
    const pct = Math.round((100 * zoneKnown[idx]) / zoneTotals[idx]);
    const s = z.score;
    const score = s ? ` · ${REC_LABEL[s.recommendation]}${s.score != null ? ` (${s.score})` : ""}` : " · no score yet";
    z.scoreEl.textContent = `${pct}% mapped${score}`;
  });
  $("stat-mapped").textContent = `${Math.round((100 * knownCount) / N)}%`;
}

// ---------- surface report (resident view) ----------
const faulty = new Map();   // unit_id -> reason, from the verifier's zone scores

function rebuildFaulty() {
  faulty.clear();
  for (const z of zoneObjs) for (const f of z.score?.flagged ?? []) faulty.set(f.unit_id, f.reason);
}

function renderReport() {
  const list = $("report-zones");
  list.innerHTML = "";
  const counts = { outposts: 0, domes: 0, suits: 0, not_safe: 0, unknown: 0 };
  for (const z of zoneObjs) {
    const s = z.score;
    const rec = s?.recommendation ?? "unknown";
    counts[rec]++;
    const li = document.createElement("li");
    li.innerHTML = `
      <div class="zone-head">
        <span class="zone-id">${z.id}</span>
        <span class="badge ${rec}">${REC_LABEL[rec]}</span>
        <span class="zone-meta">${s?.score != null ? `score ${s.score} · ` : ""}confidence ${s ? Math.round(s.confidence * 100) : 0}%</span>
      </div>
      <div class="zone-verdict">${esc(s?.verdict ?? "Waiting for the verifier.")}</div>
      ${s?.flagged?.length ? `<div class="zone-flag">⚠ ${s.flagged.map((f) => esc(f.unit_id)).join(", ")} flagged as faulty</div>` : ""}`;
    li.addEventListener("click", () => focusZone(z));
    list.appendChild(li);
  }
  const measured = zoneObjs.length - counts.unknown;
  const survivable = counts.outposts + counts.domes + counts.suits;
  $("report-summary").textContent = measured === 0
    ? "Not enough data yet."
    : `Survivable in ${survivable} of ${measured} measured zone${measured > 1 ? "s" : ""}` +
      (counts.outposts ? `, open-air outposts possible in ${counts.outposts}` : "") +
      (counts.unknown ? `. ${counts.unknown} not measured yet.` : ".");
}

// ---------- investigations ----------
let investigations = [];
const requested = new Set();            // investigation ids this viewer already sent a machine for
const invMarkers = new Map();           // id -> {group, column, ring, line, colour}
const INV_COLOUR = { open: 0xf5a524, dispatched: 0x8fd3ff, refuted: 0xff5a5f, confirmed: 0x4cc38a, expired: 0x6b7068 };
const INV_LABEL = { open: "Needs checking", dispatched: "Machine on the way", refuted: "Lie caught", confirmed: "Confirmed real", expired: "Expired" };

function nearestScout(target) {
  const t = toWorld(target.x, target.y);
  let best = null;
  for (const u of units.values()) {
    if (u.kind !== "scout_robot" || faulty.has(u.id)) continue;
    const d = u.target.distanceTo(t);
    if (!best || d < best.d) best = { u, d };
  }
  return best;
}

function renderInvestigations() {
  const box = $("investigations");
  box.innerHTML = "";
  // Active ones first, then the latest few resolved, newest first.
  const shown = [...investigations].reverse()
    .filter((i) => i.status !== "expired")
    .sort((a, b) => (["open", "dispatched"].includes(b.status) ? 1 : 0) - (["open", "dispatched"].includes(a.status) ? 1 : 0))
    .slice(0, 3);
  for (const inv of shown) {
    const div = document.createElement("div");
    div.className = `inv ${inv.status}`;
    const metres = (b) => `${Math.round(b.d * CELL_M)} m away`;
    const scout = inv.status === "open" ? nearestScout(inv.target) : null;
    div.innerHTML = `
      <div class="inv-head"><span>${esc(INV_LABEL[inv.status])}</span><span class="zone-meta">${esc(inv.id)} · ${esc(inv.zone)}</span></div>
      <p>${esc(inv.reason)}.</p>
      ${inv.finding ? `<p class="finding">${esc(inv.finding)}</p>` : ""}
      ${inv.status === "dispatched" ? `<p>${esc(inv.assigned)} is driving to the spot to measure independently.</p>` : ""}`;
    if (inv.status === "open") {
      const btn = document.createElement("button");
      if (!scout) {
        btn.textContent = "No scout available";
        btn.disabled = true;
      } else if (requested.has(inv.id)) {
        btn.textContent = `Sent ${scout.u.id}, waiting for its gateway…`;
        btn.disabled = true;
      } else {
        btn.textContent = `Approve: send ${scout.u.id} to check (${metres(scout)})`;
        btn.addEventListener("click", (e) => {
          e.stopPropagation();
          const cmd = Uplink.sendCommand({
            unitId: scout.u.id,
            type: "investigate",
            params: { x: inv.target.x, y: inv.target.y, investigation: inv.id },
            taskId: `task-${inv.id}`,
          });
          if (cmd) {
            requested.add(inv.id);
            ticker(`Approved: ${scout.u.id} sent to check ${inv.suspect}'s claim (${inv.id})`, "info");
            renderInvestigations();
          }
        });
      }
      div.appendChild(btn);
    }
    div.addEventListener("click", () => {
      const z = zoneObjs.find((o) => o.id === inv.zone);
      if (z) focusZone(z);
    });
    box.appendChild(div);
  }
}

function syncInvestigationMarkers() {
  const now = performance.now();
  for (const inv of investigations) {
    let m = invMarkers.get(inv.id);
    if (!m) {
      const group = new THREE.Group();
      group.position.copy(toWorld(inv.target.x, inv.target.y));
      const column = new THREE.Mesh(
        new THREE.CylinderGeometry(0.5, 0.5, 18, 20, 1, true),
        new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.18, blending: THREE.AdditiveBlending, depthWrite: false, side: THREE.DoubleSide }),
      );
      column.position.y = 9;
      const ring = new THREE.Mesh(
        new THREE.RingGeometry(1.6, 2.0, 40),
        new THREE.MeshBasicMaterial({ transparent: true, opacity: 0.8, side: THREE.DoubleSide, depthWrite: false }),
      );
      ring.rotation.x = -Math.PI / 2;
      ring.position.y = 0.15;
      group.add(column, ring);
      const lineGeo = new THREE.BufferGeometry().setFromPoints([new THREE.Vector3(), new THREE.Vector3()]);
      const line = new THREE.Line(lineGeo, new THREE.LineDashedMaterial({ color: 0x8fd3ff, dashSize: 0.6, gapSize: 0.4 }));
      line.visible = false;
      scene.add(group, line);
      m = { group, column, ring, line, resolvedAt: null };
      invMarkers.set(inv.id, m);
    }
    const colour = INV_COLOUR[inv.status] ?? 0xf5a524;
    m.column.material.color.set(colour);
    m.ring.material.color.set(colour);
    m.status = inv.status;
    m.assigned = inv.assigned;
    if (!["open", "dispatched"].includes(inv.status) && !m.resolvedAt) m.resolvedAt = now;
  }
}

function animateInvestigations(now) {
  for (const [id, m] of invMarkers) {
    const active = m.status === "open" || m.status === "dispatched";
    const pulse = 0.5 + 0.5 * Math.sin(now / 300);
    // Resolved markers stay 30 s so the outcome is visible, then fade out.
    const fade = m.resolvedAt ? Math.max(0, 1 - (now - m.resolvedAt) / 30000) : 1;
    m.column.material.opacity = (active ? 0.12 + 0.12 * pulse : 0.25) * fade;
    m.ring.material.opacity = (active ? 0.4 + 0.5 * pulse : 0.8) * fade;
    m.ring.scale.setScalar(active ? 1 + 0.15 * pulse : 1);
    const scout = m.status === "dispatched" ? units.get(m.assigned) : null;
    m.line.visible = !!scout;
    if (scout) {
      const pos = m.line.geometry.attributes.position;
      pos.setXYZ(0, scout.holder.position.x, 0.4, scout.holder.position.z);
      pos.setXYZ(1, m.group.position.x, 0.4, m.group.position.z);
      pos.needsUpdate = true;
      m.line.computeLineDistances();
    }
    if (fade === 0) {
      scene.remove(m.group, m.line);
      invMarkers.delete(id);
    }
  }
}

let cameraTween = null;
function focusZone(z) {
  const centre = toWorld(z.mx + ZONE_W / 2, z.my + ZONE_H / 2);
  controls.autoRotate = false;
  $("t-rotate").checked = false;
  cameraTween = {
    t0: performance.now(),
    fromTarget: controls.target.clone(), toTarget: centre,
    fromPos: camera.position.clone(), toPos: centre.clone().add(new THREE.Vector3(0, 26, 24)),
  };
}

// ---------- machines ----------
const mat = {
  metal: new THREE.MeshStandardMaterial({ color: 0x9a9d95, metalness: 0.6, roughness: 0.45 }),
  dark: new THREE.MeshStandardMaterial({ color: 0x2a2d29, metalness: 0.3, roughness: 0.7 }),
  hazard: new THREE.MeshStandardMaterial({ color: 0xd9a92a, metalness: 0.2, roughness: 0.55 }),
  teal: new THREE.MeshStandardMaterial({ color: 0x3d8f8a, metalness: 0.3, roughness: 0.5 }),
};
const mesh = (geo, m, x = 0, y = 0, z = 0) => {
  const o = new THREE.Mesh(geo, m);
  o.position.set(x, y, z);
  o.castShadow = true;
  return o;
};

function buildModel(kind) {
  const g = new THREE.Group();
  const spinners = [];
  let top = 1;
  switch (kind) {
    case "scout_robot": {
      g.add(mesh(new THREE.BoxGeometry(0.9, 0.32, 0.6), mat.hazard, 0, 0.36, 0));
      for (const [x, z] of [[0.3, 0.33], [-0.3, 0.33], [0.3, -0.33], [-0.3, -0.33]]) {
        const w = mesh(new THREE.CylinderGeometry(0.17, 0.17, 0.12, 12), mat.dark, x, 0.17, z);
        w.rotation.x = Math.PI / 2;
        g.add(w);
      }
      g.add(mesh(new THREE.CylinderGeometry(0.04, 0.04, 0.35), mat.metal, 0.2, 0.68, 0));
      const lidar = mesh(new THREE.CylinderGeometry(0.13, 0.13, 0.12, 10), mat.teal, 0.2, 0.9, 0);
      g.add(lidar);
      spinners.push([lidar, 6]);
      g.add(mesh(new THREE.BoxGeometry(0.12, 0.1, 0.4), mat.dark, 0.47, 0.42, 0)); // front sensor bar
      top = 1.15;
      break;
    }
    case "air_sensor": {
      g.add(mesh(new THREE.CylinderGeometry(0.06, 0.09, 2.2), mat.metal, 0, 1.1, 0));
      g.add(mesh(new THREE.BoxGeometry(0.55, 0.38, 0.55), mat.teal, 0, 2.3, 0));
      const fan = new THREE.Group();
      fan.position.y = 2.56;
      fan.add(mesh(new THREE.BoxGeometry(0.7, 0.03, 0.08), mat.dark));
      fan.add(mesh(new THREE.BoxGeometry(0.08, 0.03, 0.7), mat.dark));
      g.add(fan);
      spinners.push([fan, 3]);
      top = 2.8;
      break;
    }
    case "radiation_probe": {
      g.add(mesh(new THREE.CylinderGeometry(0.45, 0.55, 0.35, 10), mat.dark, 0, 0.18, 0));
      g.add(mesh(new THREE.CylinderGeometry(0.2, 0.2, 1.3, 10), mat.hazard, 0, 1.0, 0));
      const dish = new THREE.Group();
      dish.position.y = 1.75;
      const cone = mesh(new THREE.ConeGeometry(0.45, 0.3, 14, 1, true), mat.metal);
      cone.material = mat.metal;
      cone.rotation.x = Math.PI;
      dish.add(cone);
      g.add(dish);
      spinners.push([dish, 1.2]);
      top = 2.1;
      break;
    }
    case "weather_mast": {
      g.add(mesh(new THREE.CylinderGeometry(0.05, 0.08, 3.6), mat.metal, 0, 1.8, 0));
      g.add(mesh(new THREE.BoxGeometry(0.9, 0.05, 0.05), mat.metal, 0, 3.0, 0));
      g.add(mesh(new THREE.BoxGeometry(0.25, 0.3, 0.2), mat.teal, 0.42, 2.85, 0));
      const cups = new THREE.Group();
      cups.position.y = 3.65;
      for (let k = 0; k < 3; k++) {
        const a = (k / 3) * Math.PI * 2;
        const arm = mesh(new THREE.BoxGeometry(0.5, 0.02, 0.02), mat.metal, Math.cos(a) * 0.25, 0, Math.sin(a) * 0.25);
        arm.rotation.y = -a;
        cups.add(arm);
        cups.add(mesh(new THREE.SphereGeometry(0.07, 8, 6), mat.dark, Math.cos(a) * 0.5, 0, Math.sin(a) * 0.5));
      }
      g.add(cups);
      spinners.push([cups, 5]);
      top = 3.9;
      break;
    }
    case "power_station": {
      g.add(mesh(new THREE.BoxGeometry(2, 1.1, 1.5), mat.dark, 0, 0.55, 0));
      g.add(mesh(new THREE.CylinderGeometry(0.18, 0.22, 1.6, 10), mat.metal, 0.6, 1.6, 0.35));
      g.add(mesh(new THREE.CylinderGeometry(0.18, 0.22, 1.3, 10), mat.metal, 0.6, 1.45, -0.35));
      g.add(mesh(new THREE.BoxGeometry(0.6, 0.4, 1.2), mat.hazard, -0.5, 1.3, 0));
      top = 2.6;
      break;
    }
    default: {
      g.add(mesh(new THREE.BoxGeometry(0.7, 0.7, 0.7), mat.metal, 0, 0.35, 0));
      top = 1.0;
    }
  }
  return { model: g, spinners, top };
}

const GREEN = new THREE.Color(0x4cc38a), RED = new THREE.Color(0xff5a5f), AMBER = new THREE.Color(0xf5a524);
const units = new Map();
const hitboxes = [];
let unmappedCount = 0;
let rejectedTotal = 0;

// Radial gradient texture shared by every sensor field.
const fieldTexture = (() => {
  const c = document.createElement("canvas");
  c.width = c.height = 128;
  const ctx = c.getContext("2d");
  const grad = ctx.createRadialGradient(64, 64, 0, 64, 64, 64);
  grad.addColorStop(0, "rgba(255,255,255,1)");
  grad.addColorStop(0.45, "rgba(255,255,255,0.45)");
  grad.addColorStop(1, "rgba(255,255,255,0)");
  ctx.fillStyle = grad;
  ctx.fillRect(0, 0, 128, 128);
  return new THREE.CanvasTexture(c);
})();

function makeField() {
  const m = new THREE.Mesh(
    new THREE.PlaneGeometry(1, 1),
    new THREE.MeshBasicMaterial({
      map: fieldTexture, transparent: true, depthWrite: false, blending: THREE.AdditiveBlending, opacity: 0,
    }),
  );
  m.rotation.x = -Math.PI / 2;
  m.position.y = 0.09;
  m.visible = false;
  scene.add(m);
  return m;
}

function createUnit(id, kind) {
  const { model, spinners, top } = buildModel(kind);
  const holder = new THREE.Group();
  holder.add(model);

  const beacon = new THREE.Mesh(
    new THREE.SphereGeometry(0.16, 12, 8),
    new THREE.MeshBasicMaterial({ color: GREEN }),
  );
  beacon.position.y = top + 0.25;
  holder.add(beacon);

  const ring = new THREE.Mesh(
    new THREE.RingGeometry(0.75, 0.9, 32),
    new THREE.MeshBasicMaterial({ color: GREEN, transparent: true, opacity: 0.7, side: THREE.DoubleSide, depthWrite: false }),
  );
  ring.rotation.x = -Math.PI / 2;
  ring.position.y = 0.08;
  holder.add(ring);

  const hit = new THREE.Mesh(
    new THREE.CylinderGeometry(1, 1, top + 0.6, 8),
    new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false }),
  );
  hit.position.y = (top + 0.6) / 2;
  hit.userData.unitId = id;
  holder.add(hit);
  hitboxes.push(hit);

  const labelEl = document.createElement("div");
  labelEl.className = "unit-label";
  labelEl.textContent = id;
  const label = new CSS2DObject(labelEl);
  label.position.y = top + 0.9;
  holder.add(label);

  let sweep = null;
  if (kind === "scout_robot") {
    // LiDAR sweep: a faint disc the size of the scan radius with a rotating wedge.
    sweep = new THREE.Group();
    const disc = new THREE.Mesh(
      new THREE.CircleGeometry(6, 48),
      new THREE.MeshBasicMaterial({ color: 0x8fd3ff, transparent: true, opacity: 0.05, depthWrite: false }),
    );
    const wedge = new THREE.Mesh(
      new THREE.CircleGeometry(6, 24, 0, 0.6),
      new THREE.MeshBasicMaterial({ color: 0x8fd3ff, transparent: true, opacity: 0.18, depthWrite: false, blending: THREE.AdditiveBlending }),
    );
    disc.rotation.x = wedge.rotation.x = -Math.PI / 2;
    sweep.add(disc, wedge);
    sweep.position.y = 0.1;
    sweep.userData.wedge = wedge;
    holder.add(sweep);
  }

  scene.add(holder);
  const u = {
    id, kind, holder, model, spinners, beacon, ring, labelEl, sweep,
    reading: null, verified: false, reason: null, rejectedUntil: 0,
    target: new THREE.Vector3(), heading: 0, spawnAt: performance.now(), placed: false,
    radField: makeField(), airField: makeField(), cellsMapped: 0,
  };
  units.set(id, u);
  $("empty").hidden = true;
  return u;
}

function setUnitPosition(u, reading) {
  let world;
  if (reading.location) {
    world = toWorld(reading.location.x, reading.location.y);
    // Fixed sensors sit on whatever the scouts found there (e.g. a rooftop).
    if (u.kind !== "scout_robot") world.y = surfaceTopAt(reading.location.x, reading.location.y);
  } else {
    if (!u.dockSlot) u.dockSlot = ++unmappedCount;
    world = new THREE.Vector3(-GW / 2 + 2 + (u.dockSlot - 1) * 3.5, 0, GH / 2 + 4);
  }
  u.target.copy(world);
  if (!u.placed) {
    u.holder.position.copy(world);
    u.placed = true;
  }
  if (typeof reading.heading_deg === "number") u.heading = -THREE.MathUtils.degToRad(reading.heading_deg);
}

function updateFields(u) {
  const r = u.reading?.readings ?? {};
  const pos = u.target;
  // Never paint a hazard from a sensor the verifier has flagged as faulty.
  const show = $("t-rad").checked && !faulty.has(u.id);
  u.radField.visible = u.airField.visible = false;
  if (typeof r.radiation_usv_h === "number") {
    const v = r.radiation_usv_h;
    const size = 6 + 7 * Math.log10(1 + v * 4);
    u.radField.scale.set(size, size, 1);
    u.radField.material.color.set(v < 0.5 ? 0x9be15d : v < 5 ? 0xf5a524 : 0xff3b30);
    u.radField.material.opacity = v < 0.5 ? 0.35 : 0.6;
    u.radField.position.set(pos.x, u.radField.position.y, pos.z);
    u.radField.visible = show;
  }
  if (typeof r.air_quality_aqi === "number") {
    const a = r.air_quality_aqi;
    const size = 9;
    u.airField.scale.set(size, size, 1);
    u.airField.material.color.set(a <= 50 ? 0x4cc38a : a <= 100 ? 0xc9b458 : a <= 200 ? 0xc77b3a : 0x8e3b8a);
    u.airField.material.opacity = 0.25 + Math.min(0.35, a / 600);
    u.airField.position.set(pos.x, u.airField.position.y, pos.z);
    u.airField.visible = show;
  }
}

function flashBeam(position) {
  const beam = new THREE.Mesh(
    new THREE.CylinderGeometry(0.25, 0.25, 30, 12, 1, true),
    new THREE.MeshBasicMaterial({ color: 0xff3b30, transparent: true, opacity: 0.8, blending: THREE.AdditiveBlending, depthWrite: false }),
  );
  beam.position.set(position.x, 15, position.z);
  beam.userData.bornAt = performance.now();
  scene.add(beam);
  beams.push(beam);
}
const beams = [];

// ---------- HUD ----------
function ticker(text, cls = "info") {
  const li = document.createElement("li");
  const t = document.createElement("time");
  t.textContent = new Date().toLocaleTimeString();
  const span = document.createElement("span");
  span.className = cls;
  span.textContent = text;
  li.append(t, span);
  const list = $("ticker");
  list.prepend(li);
  while (list.children.length > 6) list.lastChild.remove();
}

function renderStats() {
  let verified = 0;
  for (const u of units.values()) if (u.verified && performance.now() > u.rejectedUntil) verified++;
  $("stat-units").textContent = units.size;
  $("stat-verified").textContent = verified;
  $("stat-rejected").textContent = rejectedTotal;
}

let selectedId = null;
const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function renderPanel() {
  const u = units.get(selectedId);
  const panel = $("panel");
  if (!u) { panel.hidden = true; return; }
  panel.hidden = false;
  const r = u.reading;
  const now = performance.now();
  const rows = r ? Object.entries(r.readings).map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join("") : "";
  const sig = (now < u.rejectedUntil
    ? `<div class="sig bad">✗ Last message rejected: ${esc(u.reason)}</div>`
    : u.verified ? `<div class="sig ok">✓ Signed by ${esc(r.gateway_id)}, verified</div>` : `<div class="sig bad">✗ ${esc(u.reason ?? "unverified")}</div>`)
    + (faulty.has(u.id) ? `<div class="sig warn">⚠ Flagged faulty by the verifier: ${esc(faulty.get(u.id))}. Its readings are left out of zone scores.</div>` : "");
  const loc = r?.location ? `${Math.round(r.location.x)}, ${Math.round(r.location.y)}` : "unmapped";
  $("panel-body").innerHTML = `
    <h2>${esc(u.id)}</h2>
    <div class="kind">${esc((u.kind ?? "unknown").replace(/_/g, " "))} · ${esc(r?.protocol ?? "?")} · ${esc(r?.zone ?? "?")}</div>
    ${sig}
    <h3>Readings</h3>
    <dl>${rows || "<dt>none yet</dt><dd></dd>"}</dl>
    <h3>Where</h3>
    <dl><dt>Map position</dt><dd>${esc(loc)}</dd><dt>Gateway</dt><dd>${esc(r?.gateway_id ?? "?")}</dd>
    ${u.kind === "scout_robot" ? `<dt>Cells surveyed</dt><dd>${u.cellsMapped}</dd>` : ""}
    <dt>Last update</dt><dd>${esc(r?.timestamp?.slice(11, 19) ?? "?")} UTC</dd></dl>
    ${u.fingerprint ? `<h3>Fingerprint</h3><p class="note">${esc(u.fingerprint.evidence)} (confidence ${u.fingerprint.confidence})</p>` : ""}
    <h3>Tasks</h3>
    <p class="note">${u.kind === "scout_robot"
      ? "Scouts take investigation tasks from the Surface report. Zone goals come next."
      : "This machine only reports; it can't be tasked."}</p>`;
}
$("panel-close").addEventListener("click", () => { selectedId = null; renderPanel(); });

// ---------- live data ----------
const fingerprints = new Map();
const STATUS = { live: ["live", "pill-ok"], reconnecting: ["reconnecting…", "pill-wait"], offline: ["offline", "pill-bad"] };

Uplink.start({
  onStatus(state) {
    const [text, cls] = STATUS[state];
    $("conn").textContent = text;
    $("conn").className = `pill ${cls}`;
  },
  onLog: ticker,
  onDiscovered(d) {
    fingerprints.set(d.unit_id, d.fingerprint);
    const u = units.get(d.unit_id);
    if (u) u.fingerprint = d.fingerprint;
    ticker(`Discovered ${d.unit_id} via ${d.protocol} at ${d.gateway_id}: ${d.fingerprint?.kind}`, "info");
  },
  onReading({ unitId, msg, ok, reason }) {
    const now = performance.now();
    let u = units.get(unitId);
    if (!ok) {
      rejectedTotal++;
      ticker(`Rejected reading for ${unitId}: ${reason}`, "bad");
      if (u) {
        u.reason = reason;
        u.rejectedUntil = now + REJECT_HOLD_MS;
        flashBeam(u.holder.position);
      }
      renderStats();
      if (selectedId === unitId) renderPanel();
      return;
    }
    if (!u) {
      u = createUnit(unitId, msg.kind);
      u.fingerprint = fingerprints.get(unitId);
    }
    u.reading = msg;
    u.verified = true;
    if (now > u.rejectedUntil) u.reason = null;
    if (msg.survey) {
      applySurvey(msg.survey, unitId, now);
      u.cellsMapped += msg.survey.cells.length;
    }
    setUnitPosition(u, msg);
    updateFields(u);
    renderStats();
    if (selectedId === unitId) renderPanel();
  },
  onSnapshot(snap) {
    applySnapshot(snap, performance.now());
  },
  onInvestigations(list) {
    // The verifier republishes every few seconds; only rebuild the cards when something
    // changed, so a button isn't swapped out from under someone's click.
    if (JSON.stringify(list) === JSON.stringify(investigations)) return;
    const before = new Map(investigations.map((i) => [i.id, i.status]));
    investigations = list;
    for (const inv of list) {
      const prev = before.get(inv.id);
      if (prev === inv.status) continue;
      if (!prev && inv.status === "open") ticker(`Investigation ${inv.id}: ${inv.reason}`, "bad");
      else if (inv.status === "dispatched") ticker(`${inv.id}: ${inv.assigned} is on its way`, "info");
      else if (inv.status === "refuted") ticker(`${inv.id} refuted: ${inv.finding}`, "ok");
      else if (inv.status === "confirmed") ticker(`${inv.id} confirmed: ${inv.finding}`, "info");
    }
    syncInvestigationMarkers();
    renderInvestigations();
  },
  onAck(ack) {
    const cls = ack.result === "rejected" ? "bad" : "info";
    ticker(`${ack.unit_id} ${ack.result} ${ack.task_id}${ack.reason ? ` (${ack.reason})` : ""}`, cls);
  },
  onZoneScore({ zone, msg, ok, reason }) {
    const z = zoneObjs.find((o) => o.id === zone);
    if (!z) return;
    if (!ok) { ticker(`Rejected zone score for ${zone}: ${reason}`, "bad"); return; }
    const before = new Set(faulty.keys());
    const prevRec = z.score?.recommendation;
    z.score = msg;
    rebuildFaulty();
    for (const [id, why] of faulty) if (!before.has(id)) ticker(`Verifier flagged ${id} as faulty: ${why}`, "bad");
    if (prevRec && prevRec !== msg.recommendation) ticker(`${zone} is now: ${REC_LABEL[msg.recommendation]}`, "info");
    z.tint.material.color.set(RECOMMENDATION_COLOUR[msg.recommendation] ?? 0x8fd3ff);
    z.tint.material.opacity = $("t-zones").checked && msg.recommendation !== "unknown" ? 0.1 : 0;
    units.forEach(updateFields);
    renderReport();
    if (selectedId) renderPanel();
  },
});

// ---------- toggles ----------
$("t-labels").addEventListener("change", (e) => {
  labelRenderer.domElement.style.display = e.target.checked ? "" : "none";
});
$("t-rad").addEventListener("change", () => units.forEach(updateFields));
$("t-zones").addEventListener("change", (e) => {
  for (const z of zoneObjs) {
    z.outline.visible = e.target.checked;
    z.tint.material.opacity = e.target.checked && z.score && z.score.recommendation !== "unknown" ? 0.1 : 0;
  }
});
$("t-rotate").addEventListener("change", (e) => { controls.autoRotate = e.target.checked; });

// ---------- hover + click ----------
const raycaster = new THREE.Raycaster();
const pointer = new THREE.Vector2();
let pointerEvent = null;
let downAt = null;

renderer.domElement.addEventListener("pointermove", (e) => { pointerEvent = e; });
renderer.domElement.addEventListener("pointerleave", () => { pointerEvent = null; tooltip.hidden = true; });
renderer.domElement.addEventListener("pointerdown", (e) => { downAt = [e.clientX, e.clientY]; });
renderer.domElement.addEventListener("pointerup", (e) => {
  if (!downAt || Math.hypot(e.clientX - downAt[0], e.clientY - downAt[1]) > 5) return; // it was a drag
  const hit = pick(e);
  selectedId = hit?.unit ? hit.unit.id : null;
  renderPanel();
});

function pick(e) {
  pointer.set((e.clientX / window.innerWidth) * 2 - 1, -(e.clientY / window.innerHeight) * 2 + 1);
  raycaster.setFromCamera(pointer, camera);
  const unitHit = raycaster.intersectObjects(hitboxes, false)[0];
  if (unitHit) return { unit: units.get(unitHit.object.userData.unitId) };
  const tileHit = raycaster.intersectObject(tiles, false)[0];
  if (tileHit && cellClass[tileHit.instanceId] !== -1) return { tile: tileHit.instanceId };
  return null;
}

function fmtAge(ms) {
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s} s ago` : `${Math.round(s / 60)} min ago`;
}

function updateTooltip() {
  if (!pointerEvent) return;
  const hit = pick(pointerEvent);
  if (!hit) { tooltip.hidden = true; renderer.domElement.style.cursor = ""; return; }
  let html;
  if (hit.unit) {
    const u = hit.unit;
    const r = u.reading?.readings ?? {};
    const keys = Object.entries(r).slice(0, 3).map(([k, v]) => `${esc(k)} ${esc(v)}`).join("<br>");
    const bad = performance.now() < u.rejectedUntil;
    html = `<b>${esc(u.id)}</b> <span class="muted">${esc(u.kind)}</span><br>${keys}<br>` +
      (bad ? `<span style="color:var(--bad)">✗ ${esc(u.reason)}</span>`
        : faulty.has(u.id) ? `<span style="color:var(--warn)">⚠ flagged faulty, ignored</span>`
        : `<span style="color:var(--ok)">✓ verified</span>`);
    renderer.domElement.style.cursor = "pointer";
  } else {
    const i = hit.tile;
    const cls = CLASS_NAMES[cellClass[i]];
    const h = cellHeightM[i] > 0.5 ? ` · ${Math.round(cellHeightM[i])} m` : "";
    html = `<b>${CLASS_LABEL[cls]}</b>${h}<br><span class="muted">scanned ${fmtAge(performance.now() - cellSeen[i])} by ${esc(cellScanner[i])}</span>`;
    renderer.domElement.style.cursor = "";
  }
  tooltip.innerHTML = html;
  tooltip.hidden = false;
  tooltip.style.left = `${pointerEvent.clientX + 14}px`;
  tooltip.style.top = `${pointerEvent.clientY + 14}px`;
}

// ---------- dust in the air ----------
const dust = (() => {
  const count = 700;
  const pos = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    pos[i * 3] = (Math.random() - 0.5) * 100;
    pos[i * 3 + 1] = Math.random() * 18;
    pos[i * 3 + 2] = (Math.random() - 0.5) * 70;
  }
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  const pts = new THREE.Points(geo, new THREE.PointsMaterial({ color: 0xbfae8c, size: 0.12, transparent: true, opacity: 0.45, depthWrite: false }));
  scene.add(pts);
  return pts;
})();

// ---------- frame loop ----------
const clock = new THREE.Clock();
let lastFade = 0, lastTooltip = 0, lastLabels = 0;

function animate() {
  const dt = Math.min(clock.getDelta(), 0.1);
  const now = performance.now();

  // Newly surveyed tiles rise out of the dark.
  if (revealing.size) {
    for (const [i, start] of revealing) {
      const k = Math.min(1, (now - start) / 600);
      placeTile(i, Math.max(0, 1 - Math.pow(1 - Math.max(k, 0), 3)));
      if (k >= 1) revealing.delete(i);
    }
    markMatrices();
  }

  // Once a second: fade tiles whose last scan is getting old, refresh labels.
  if (now - lastFade > 1000) {
    for (let i = 0; i < N; i++) if (cellClass[i] !== -1) colourTile(i, now);
    markColours();
    lastFade = now;
  }
  if (now - lastLabels > 500) { renderZoneLabels(); renderStats(); lastLabels = now; }
  animateInvestigations(now);

  for (const u of units.values()) {
    // Smooth movement toward the latest reported position; spawn drop on arrival.
    u.holder.position.lerp(u.target, 1 - Math.exp(-dt * 5));
    const spawnK = Math.min(1, (now - u.spawnAt) / 900);
    u.model.position.y = (1 - (1 - Math.pow(1 - spawnK, 3))) * 10;
    u.model.rotation.y += (u.heading - u.model.rotation.y) * Math.min(1, dt * 6);

    for (const [obj, speed] of u.spinners) obj.rotation.y += speed * dt;
    if (u.sweep) u.sweep.userData.wedge.rotation.z -= dt * 3;

    const bad = now < u.rejectedUntil;
    const isFaulty = faulty.has(u.id);
    const colour = bad ? RED : isFaulty || (u.fingerprint?.confidence ?? 1) < 0.5 ? AMBER : GREEN;
    u.labelEl.classList.toggle("faulty", isFaulty && !bad);
    const labelText = isFaulty ? `${u.id} · faulty` : u.id;
    if (u.labelEl.textContent !== labelText) u.labelEl.textContent = labelText;
    u.beacon.material.color.copy(colour);
    u.ring.material.color.copy(colour);
    const pulse = 0.5 + 0.5 * Math.sin(now / (bad ? 120 : 500));
    u.ring.material.opacity = 0.35 + 0.45 * pulse;
    u.ring.scale.setScalar(spawnK < 1 ? 1 + (1 - spawnK) * 6 : 1 + 0.08 * pulse);
    u.labelEl.classList.toggle("bad", bad);
  }

  for (let k = beams.length - 1; k >= 0; k--) {
    const b = beams[k];
    const age = (now - b.userData.bornAt) / 1500;
    b.material.opacity = 0.8 * (1 - age);
    if (age >= 1) { scene.remove(b); b.geometry.dispose(); b.material.dispose(); beams.splice(k, 1); }
  }

  const d = dust.geometry.attributes.position;
  for (let i = 0; i < d.count; i++) {
    let x = d.getX(i) + dt * 0.6;
    if (x > 50) x = -50;
    d.setX(i, x);
  }
  d.needsUpdate = true;

  if (now - lastTooltip > 60) { updateTooltip(); lastTooltip = now; }

  if (cameraTween) {
    const k = Math.min(1, (now - cameraTween.t0) / 900);
    const e = 1 - Math.pow(1 - k, 3);
    controls.target.lerpVectors(cameraTween.fromTarget, cameraTween.toTarget, e);
    camera.position.lerpVectors(cameraTween.fromPos, cameraTween.toPos, e);
    if (k >= 1) cameraTween = null;
  }

  controls.update(dt);
  renderer.render(scene, camera);
  labelRenderer.render(scene, camera);
  requestAnimationFrame(animate);
}
requestAnimationFrame(animate);
renderReport();

window.addEventListener("resize", () => {
  camera.aspect = window.innerWidth / window.innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(window.innerWidth, window.innerHeight);
  labelRenderer.setSize(window.innerWidth, window.innerHeight);
});
