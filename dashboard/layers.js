// Map layers, shared by the 3D surface view and the operator view.
//
// A layer shows one kind of data on its own, like a floor selector on a map: radiation,
// air quality, temperature, the other air readings, terrain, or how old each scan is.
// Every coloured cell is a measurement some verified machine made in that 5 m cell;
// cells nobody measured stay grey and smaller, and cells nobody has seen stay dark.
//
// Colours follow the job they do:
//   status bands (good / warning / serious / critical) for readings that mean safe or not,
//   a two-sided ramp around 22 °C for temperature, a one-hue ramp for plain amounts,
//   and three hues plus greys for terrain (validated colour-blind safe on the dark map).
window.Layers = (() => {
  const GW = 80, GH = 50, CELL = 10, N = GW * GH;   // must match hollow/geo.py

  const STATUS = { good: "#0ca30c", warning: "#fab219", serious: "#ec835a", critical: "#d03b3b" };
  const UNMEASURED = "#2a2b28";      // surveyed, but nothing measured this here
  // One-hue ramp, dark to light: on the dark map, small amounts recede and large ones stand out.
  const BLUE = ["#0d366b", "#104281", "#184f95", "#1c5cab", "#256abf", "#2a78d6", "#3987e5",
                "#5598e7", "#6da7ec", "#86b6ef", "#9ec5f4", "#b7d3f6", "#cde2fb"];

  // ---------- colour maths (OKLab, for the two-sided temperature ramp) ----------
  const hexToRgb = (h) => [1, 3, 5].map((k) => parseInt(h.slice(k, k + 2), 16));
  const rgbToHex = (rgb) => "#" + rgb.map((c) => Math.round(Math.min(255, Math.max(0, c))).toString(16).padStart(2, "0")).join("");
  const toLin = (c) => { c /= 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; };
  const fromLin = (c) => 255 * (c <= 0.0031308 ? 12.92 * c : 1.055 * c ** (1 / 2.4) - 0.055);
  function toOklab(hex) {
    const [r, g, b] = hexToRgb(hex).map(toLin);
    const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
    const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
    const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
    return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s,
            1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s,
            0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
  }
  function fromOklab([L, a, b]) {
    const l = (L + 0.3963377774 * a + 0.2158037573 * b) ** 3;
    const m = (L - 0.1055613458 * a - 0.0638541728 * b) ** 3;
    const s = (L - 0.0894841775 * a - 1.291485548 * b) ** 3;
    return rgbToHex([
      4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
      -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
      -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
    ].map(fromLin));
  }
  const mix = (a, b, t) => { const A = toOklab(a), B = toOklab(b); return fromOklab(A.map((v, k) => v + (B[k] - v) * t)); };
  // Cold pole, grey midpoint, hot pole; equal steps per arm.
  const ARM = 4;
  const DIVERGING = [
    ...[4, 3, 2, 1].map((k) => mix("#383835", "#3987e5", k / ARM)),
    "#383835",
    ...[1, 2, 3, 4].map((k) => mix("#383835", "#e66767", k / ARM)),
  ];

  // ---------- the layers ----------
  const LAYERS = [
    { id: "all", label: "Everything", kind: "all", group: 0,
      blurb: "Terrain, machines, sensor glows and zone safety together. Pick a layer to see one kind of data on its own." },
    { id: "radiation", label: "Radiation", kind: "status", group: 1, quantity: "radiation_usv_h", unit: "µSv/h", digits: 2,
      bands: [[0.3, "good", "Normal"], [1, "warning", "Slightly raised"], [5, "serious", "Elevated"], [Infinity, "critical", "High or dangerous"]] },
    { id: "air", label: "Air quality", kind: "status", group: 1, quantity: "air_quality_aqi", unit: "AQI", digits: 0,
      bands: [[50, "good", "Good"], [100, "warning", "Moderate"], [200, "serious", "Poor to unhealthy"], [Infinity, "critical", "Hazardous"]] },
    { id: "temperature", label: "Temperature", kind: "diverging", group: 2, quantity: "temp_c", unit: "°C", digits: 1,
      centre: 22, step: 3 },
    { id: "pm25", label: "PM2.5", kind: "status", group: 3, quantity: "pm25_ugm3", unit: "µg/m³", digits: 1,
      bands: [[9, "good", "Good"], [35.4, "warning", "Moderate"], [55.4, "serious", "Unhealthy for some"], [Infinity, "critical", "Unhealthy"]] },
    { id: "co2", label: "CO₂", kind: "status", group: 3, quantity: "co2_ppm", unit: "ppm", digits: 0,
      bands: [[1000, "good", "Normal"], [2000, "warning", "Stuffy"], [5000, "serious", "Poor"], [Infinity, "critical", "Dangerous"]] },
    { id: "humidity", label: "Humidity", kind: "sequential", group: 3, quantity: "humidity_pct", unit: "%", digits: 0, min: 0, max: 100 },
    { id: "wind", label: "Wind", kind: "sequential", group: 3, quantity: "wind_kph", unit: "km/h", digits: 1, min: 0, max: 60 },
    { id: "terrain", label: "Terrain", kind: "terrain", group: 4 },
    { id: "age", label: "Scan age", kind: "age", group: 4, max: 600 },
  ];
  const byId = Object.fromEntries(LAYERS.map((l) => [l.id, l]));
  const QUANTITIES = LAYERS.filter((l) => l.quantity).map((l) => l.quantity);
  const isData = (layer) => !!layer.quantity;

  const CLASS_NAMES = ["open", "rubble", "building", "building_damaged", "vegetation", "water"];
  const CLASS_LABEL = { open: "Open ground", rubble: "Rubble", building: "Building",
    building_damaged: "Damaged building", vegetation: "Vegetation", water: "Water" };
  const TERRAIN = { open: "#383835", rubble: "#898781", building: "#c3c2b7",
    building_damaged: "#d95926", vegetation: "#199e70", water: "#3987e5" };

  function band(layer, v) {
    for (const [limit, role, label] of layer.bands) if (v <= limit) return { role, label };
    return { role: "critical", label: "" };
  }
  function divergingIndex(layer, v) {
    const d = v - layer.centre;
    const k = Math.min(ARM, Math.round(Math.abs(d) / layer.step));
    return ARM + Math.sign(d) * k;
  }
  /** Hex colour for a value on a data layer. */
  function colourOf(layer, v) {
    if (layer.kind === "status") return STATUS[band(layer, v).role];
    if (layer.kind === "diverging") return DIVERGING[divergingIndex(layer, v)];
    const t = Math.min(1, Math.max(0, (v - layer.min) / (layer.max - layer.min)));
    return BLUE[Math.round(t * (BLUE.length - 1))];
  }
  /** "0.14 µSv/h · Normal" */
  function describe(layer, v) {
    const value = `${v.toFixed(layer.digits)} ${layer.unit}`;
    if (layer.kind === "status") return `${value} · ${band(layer, v).label}`;
    if (layer.kind === "diverging") {
      const d = v - layer.centre;
      return `${value} · ${Math.abs(d) < layer.step / 2 ? "comfortable" : d < 0 ? "cool" : "warm"}`;
    }
    return value;
  }
  /** Fresh scans are light, old ones sink toward the dark. */
  const ageColour = (ageS) => BLUE[Math.round((1 - Math.min(1, Math.max(0, ageS) / byId.age.max)) * (BLUE.length - 1))];
  const terrainColour = (cls) => TERRAIN[cls] ?? UNMEASURED;

  function fmtAge(ms) {
    const s = Math.max(0, Math.round(ms / 1000));
    return s < 60 ? `${s} s ago` : `${Math.round(s / 60)} min ago`;
  }

  // ---------- per-cell measurements ----------
  /** Latest trusted value of each quantity in each survey cell. `now` is whatever clock the page uses. */
  class MeasureStore {
    constructor() {
      this.q = {};
      for (const q of QUANTITIES) this.q[q] = { v: new Float32Array(N).fill(NaN), seen: new Float64Array(N), unit: new Array(N), count: 0 };
    }
    get(quantity, i) {
      const s = this.q[quantity];
      return s && !Number.isNaN(s.v[i]) ? { v: s.v[i], seen: s.seen[i], unit: s.unit[i] } : null;
    }
    count(quantity) { return this.q[quantity]?.count ?? 0; }
    set(quantity, i, v, seen, unit) {
      const s = this.q[quantity];
      if (!s) return false;
      if (Number.isNaN(s.v[i])) s.count++;
      const changed = s.v[i] !== v || s.unit[i] !== unit;
      s.v[i] = v; s.seen[i] = seen; s.unit[i] = unit;
      return changed;
    }
    /** A verified reading arriving live. Returns the cell index if anything changed, else -1. */
    addReading(msg, now, isFaulty) {
      const loc = msg.location;
      if (!loc || isFaulty) return -1;
      const gx = Math.floor(loc.x / CELL), gy = Math.floor(loc.y / CELL);
      if (gx < 0 || gy < 0 || gx >= GW || gy >= GH) return -1;
      const i = gy * GW + gx;
      let changed = false;
      for (const q of QUANTITIES) {
        const v = msg.readings?.[q];
        if (typeof v === "number" && Number.isFinite(v)) changed = this.set(q, i, v, now, msg.unit_id) || changed;
      }
      return changed ? i : -1;
    }
    /** The verifier's signed snapshot. Keeps our own newer values. Returns changed cell indexes. */
    applySnapshot(measures, now, faulty) {
      const changed = new Set();
      const units = measures?.units ?? [];
      for (const [q, rows] of Object.entries(measures?.quantities ?? {})) {
        const s = this.q[q];
        if (!s || !Array.isArray(rows)) continue;
        for (const [gx, gy, v, ageS, ui] of rows) {
          if (gx < 0 || gy < 0 || gx >= GW || gy >= GH || typeof v !== "number") continue;
          const i = gy * GW + gx;
          const seen = now - ageS * 1000;
          if (!Number.isNaN(s.v[i]) && s.seen[i] >= seen && !faulty.has(s.unit[i])) continue;
          if (this.set(q, i, v, seen, units[ui] ?? "?")) changed.add(i);
        }
      }
      return [...changed];
    }
    /** Forget everything measured by machines the verifier now flags. Returns changed cell indexes. */
    purge(faulty) {
      const changed = new Set();
      if (!faulty.size) return [];
      for (const s of Object.values(this.q)) {
        for (let i = 0; i < N; i++) {
          if (!Number.isNaN(s.v[i]) && faulty.has(s.unit[i])) {
            s.v[i] = NaN; s.unit[i] = undefined; s.count--;
            changed.add(i);
          }
        }
      }
      return [...changed];
    }
  }

  /** Surveyed terrain, for views that don't keep their own (the operator view). */
  class SurfaceGrid {
    constructor() {
      this.cls = new Int8Array(N).fill(-1);
      this.height = new Float32Array(N);
      this.seen = new Float64Array(N);
      this.scanner = new Array(N);
      this.known = 0;
    }
    put(gx, gy, h, cls, seen, scanner) {
      const ci = CLASS_NAMES.indexOf(cls);
      if (ci < 0 || gx < 0 || gy < 0 || gx >= GW || gy >= GH) return;
      const i = gy * GW + gx;
      if (this.cls[i] !== -1 && this.seen[i] > seen) return;
      if (this.cls[i] === -1) this.known++;
      this.cls[i] = ci; this.height[i] = h; this.seen[i] = seen; this.scanner[i] = scanner;
    }
    applySurvey(survey, scanner, now) {
      if (!survey || survey.cell_size !== CELL || !Array.isArray(survey.cells)) return;
      for (const [gx, gy, h, cls] of survey.cells) this.put(gx, gy, h, cls, now, scanner);
    }
    applySnapshot(snap, now) {
      if (snap.cell_size !== CELL || !Array.isArray(snap.cells)) return;
      for (const [gx, gy, h, cls, ageS, scanner] of snap.cells) this.put(gx, gy, h, cls, now - ageS * 1000, scanner);
    }
  }

  // ---------- selector + legend ----------
  const ICONS = {
    all: '<path d="M12 3 3 7.8l9 4.8 9-4.8Z"/><path d="m3 12.3 9 4.8 9-4.8"/><path d="m3 16.8 9 4.8 9-4.8"/>',
    radiation: '<g fill="currentColor" stroke="none"><circle cx="12" cy="12" r="1.9"/>' +
      '<path d="M10.4 9.23 7.5 4.21A9 9 0 0 1 16.5 4.21L13.6 9.23A3.2 3.2 0 0 0 10.4 9.23Z"/>' +
      '<path d="M15.2 12H21A9 9 0 0 1 16.5 19.79L13.6 14.77A3.2 3.2 0 0 0 15.2 12Z"/>' +
      '<path d="M10.4 14.77 7.5 19.79A9 9 0 0 1 3 12H8.8A3.2 3.2 0 0 0 10.4 14.77Z"/></g>',
    air: '<path d="M4 8c2.5-2 5.5-2 8 0s5.5 2 8 0"/><path d="M4 13c2.5-2 5.5-2 8 0s5.5 2 8 0"/><path d="M4 18c2.5-2 5.5-2 8 0s5.5 2 8 0"/>',
    temperature: '<path d="M10 4.5a2 2 0 0 1 4 0v9a4 4 0 1 1-4 0Z"/><path d="M12 9v7.5"/>',
    pm25: '<g fill="currentColor" stroke="none"><circle cx="6" cy="7" r="1.7"/><circle cx="12.5" cy="5.5" r="1.2"/>' +
      '<circle cx="17.5" cy="9" r="1.9"/><circle cx="9" cy="12.5" r="1.3"/><circle cx="14.5" cy="14.5" r="1.6"/>' +
      '<circle cx="6.5" cy="18" r="1.4"/><circle cx="18.5" cy="18.5" r="1.1"/><circle cx="11.5" cy="19" r="0.9"/></g>',
    co2: '<text x="12" y="15.6" text-anchor="middle" font-size="9.5" font-weight="700" fill="currentColor" stroke="none" ' +
      'font-family="ui-sans-serif, system-ui, sans-serif">CO₂</text>',
    humidity: '<path d="M12 3.5c3.5 4.4 6 7.7 6 10.5a6 6 0 0 1-12 0c0-2.8 2.5-6.1 6-10.5Z"/>',
    wind: '<path d="M3 8h10a2.5 2.5 0 1 0-2.5-2.5"/><path d="M3 12h15a3 3 0 1 1-3 3"/><path d="M3 16h7"/>',
    terrain: '<path d="M3 20.5h18"/><path d="M5 20.5V11h4v9.5"/><path d="M9 20.5V5h5v15.5"/><path d="M14 20.5v-7h5v7"/>',
    age: '<circle cx="12" cy="12" r="8.5"/><path d="M12 7.5V12l3 2"/>',
  };
  const icon = (id) =>
    `<svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" stroke-width="1.8" ` +
    `stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[id]}</svg>`;
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const range = (lo, hi, digits) => hi === Infinity ? `> ${lo}` : lo == null ? `≤ ${hi}` : `${lo}–${hi}`;

  function legendHtml(layer) {
    const head = `<div class="legend-head">${icon(layer.id)}<b>${esc(layer.label)}</b>${layer.unit ? `<span>${esc(layer.unit)}</span>` : ""}</div>`;
    let body = "";
    if (layer.kind === "all") {
      body = `<p class="legend-note">${esc(layer.blurb)}</p>`;
    } else if (layer.kind === "status") {
      let lo = null;
      body = '<ul class="legend-bands">' + layer.bands.map(([hi, role, label]) => {
        const row = `<li><i style="background:${STATUS[role]}"></i><span>${esc(label)}</span><em>${range(lo, hi)}</em></li>`;
        lo = hi;
        return row;
      }).join("") + "</ul>";
    } else if (layer.kind === "diverging") {
      const lo = layer.centre - ARM * layer.step, hi = layer.centre + ARM * layer.step;
      body = `<div class="legend-ramp">${DIVERGING.map((c) => `<i style="background:${c}"></i>`).join("")}</div>
        <div class="legend-ticks"><span>≤ ${lo}</span><span>${layer.centre}</span><span>≥ ${hi}</span></div>
        <p class="legend-note">Grey is comfortable; blue is cooler, red is warmer.</p>`;
    } else if (layer.kind === "sequential" || layer.kind === "age") {
      const ramp = layer.kind === "age" ? [...BLUE].reverse() : BLUE;
      const [a, b] = layer.kind === "age" ? ["just scanned", `${layer.max / 60}+ min ago`] : [`${layer.min}`, `${layer.max}+`];
      body = `<div class="legend-ramp">${ramp.map((c) => `<i style="background:${c}"></i>`).join("")}</div>
        <div class="legend-ticks"><span>${a}</span><span>${b}</span></div>`;
    } else if (layer.kind === "terrain") {
      body = '<ul class="legend-bands">' + CLASS_NAMES.map((c) =>
        `<li><i style="background:${TERRAIN[c]}"></i><span>${esc(CLASS_LABEL[c])}</span></li>`).join("") + "</ul>";
    }
    const key = isData(layer)
      ? `<p class="legend-key"><i class="unmeasured"></i>Small grey cells: surveyed, not measured. Dark: never seen.</p>`
      : layer.kind === "all" ? "" : `<p class="legend-key">Dark: never seen.</p>`;
    return head + body + key + '<p class="legend-count"></p>';
  }

  /**
   * Builds the vertical layer strip and its legend.
   *   mount: element for the strip; legend: element for the legend
   *   storageKey: remembers this viewer's last layer (per page)
   *   onChange(layer): called once at start and on every change
   */
  function createSelector({ mount, legend, storageKey, onChange }) {
    let current = byId.all;
    try { current = byId[localStorage.getItem(storageKey)] ?? byId.all; } catch { /* storage blocked */ }
    mount.setAttribute("role", "radiogroup");
    mount.setAttribute("aria-label", "Map layer");
    const buttons = [];
    let group = 0;
    for (const layer of LAYERS) {
      if (layer.group !== group) {
        mount.insertAdjacentHTML("beforeend", '<span class="layer-sep" aria-hidden="true"></span>');
        group = layer.group;
      }
      const b = document.createElement("button");
      b.type = "button";
      b.className = "layer-btn";
      b.setAttribute("role", "radio");
      b.setAttribute("aria-label", layer.label);
      b.dataset.layer = layer.id;
      b.innerHTML = `${icon(layer.id)}<span class="layer-tip">${esc(layer.label)}</span>`;
      b.addEventListener("click", () => select(layer));
      b.addEventListener("keydown", (e) => {
        const step = { ArrowDown: 1, ArrowRight: 1, ArrowUp: -1, ArrowLeft: -1 }[e.key];
        if (!step) return;
        e.preventDefault();
        const next = buttons[(buttons.indexOf(b) + step + buttons.length) % buttons.length];
        next.focus();
        select(byId[next.dataset.layer]);
      });
      mount.appendChild(b);
      buttons.push(b);
    }
    function select(layer) {
      current = layer;
      for (const b of buttons) {
        const on = b.dataset.layer === layer.id;
        b.setAttribute("aria-checked", String(on));
        b.tabIndex = on ? 0 : -1;
      }
      legend.innerHTML = legendHtml(layer);
      try { localStorage.setItem(storageKey, layer.id); } catch { /* storage blocked */ }
      onChange(layer);
    }
    select(current);
    return {
      get: () => current,
      /** Short status line under the legend, e.g. "Measured in 412 of 2,960 surveyed cells". */
      setCount(text) {
        const el = legend.querySelector(".legend-count");
        if (el && el.textContent !== text) el.textContent = text;
      },
    };
  }

  return {
    GW, GH, CELL, N, LAYERS, byId, QUANTITIES, CLASS_NAMES, CLASS_LABEL, UNMEASURED,
    isData, colourOf, describe, ageColour, terrainColour, fmtAge, MeasureStore, SurfaceGrid, createSelector,
  };
})();
