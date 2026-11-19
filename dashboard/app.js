// Operator view: map, machine table and event feed. Live data and signature
// checks come from uplink.js, shared with the 3D city view.
(() => {
  const SVG_NS = "http://www.w3.org/2000/svg";

  // 3 x 2 zone grid on an 800 x 500 map. Gateways place units with x/y in this space.
  const ZONES = [
    { id: "Z1", x: 0, y: 0 }, { id: "Z2", x: 267, y: 0 }, { id: "Z3", x: 534, y: 0 },
    { id: "Z4", x: 0, y: 250 }, { id: "Z5", x: 267, y: 250 }, { id: "Z6", x: 534, y: 250 },
  ];
  const ZONE_W = 266, ZONE_H = 250;

  const units = new Map();      // unit_id -> {reading, verified, reason, fingerprint}
  const zoneScores = new Map(); // zone -> verified score message
  const faulty = new Map();     // unit_id -> reason flagged by the verifier
  let rejected = 0;

  // Map layers (layers.js): terrain and per-cell measurements, one kind of data at a time.
  const { GW, GH, CELL, N } = Layers;
  const grid = new Layers.SurfaceGrid();
  const measures = new Layers.MeasureStore();
  let layer = Layers.byId.all;
  let selector = null;
  let layerImg = null;
  const PX = 5;                                   // canvas pixels per survey cell
  const canvas = document.createElement("canvas");
  canvas.width = GW * PX;
  canvas.height = GH * PX;
  const ctx = canvas.getContext("2d");

  const $ = (id) => document.getElementById(id);
  const map = $("map");

  function el(tag, attrs = {}, text) {
    const node = document.createElementNS(SVG_NS, tag);
    for (const [k, v] of Object.entries(attrs)) node.setAttribute(k, v);
    if (text != null) node.textContent = text;
    return node;
  }

  function drawZones() {
    for (const z of ZONES) {
      map.appendChild(el("rect", { class: "zone", x: z.x + 2, y: z.y + 2, width: ZONE_W - 4, height: ZONE_H - 4, rx: 6 }));
    }
    // The selected layer, drawn as one image above the zones and below labels and machines.
    layerImg = el("image", { id: "layer-img", x: 0, y: 0, width: 800, height: 500, preserveAspectRatio: "none", visibility: "hidden" });
    map.appendChild(layerImg);
    for (const z of ZONES) {
      map.appendChild(el("text", { class: "zone-label", x: z.x + 14, y: z.y + 26 }, z.id));
    }
  }

  // ---------- map layers ----------
  let layerQueued = false;
  function queueLayer() {
    if (layerQueued) return;
    layerQueued = true;
    requestAnimationFrame(renderLayer);
  }

  function renderLayer() {
    layerQueued = false;
    updateCount();
    if (layer.kind === "all") { layerImg.setAttribute("visibility", "hidden"); return; }
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    const now = performance.now();
    for (let i = 0; i < N; i++) {
      const ci = grid.cls[i];
      let hex = null, full = true;
      if (layer.kind === "terrain") {
        if (ci < 0) continue;
        hex = Layers.terrainColour(Layers.CLASS_NAMES[ci]);
      } else if (layer.kind === "age") {
        if (ci < 0) continue;
        hex = Layers.ageColour((now - grid.seen[i]) / 1000);
      } else {
        const m = measures.get(layer.quantity, i);
        if (m) hex = Layers.colourOf(layer, m.v);
        else if (ci >= 0) { hex = Layers.UNMEASURED; full = false; }   // surveyed, not measured: small grey dot
        else continue;                                                   // never seen: leave the zone showing
      }
      const gx = i % GW, gy = Math.floor(i / GW);
      ctx.fillStyle = hex;
      if (full) ctx.fillRect(gx * PX, gy * PX, PX, PX);
      else ctx.fillRect(gx * PX + 1, gy * PX + 1, PX - 2, PX - 2);
    }
    layerImg.setAttribute("href", canvas.toDataURL());
    layerImg.setAttribute("visibility", "visible");
  }

  function updateCount() {
    if (!selector) return;
    const fmt = (n) => n.toLocaleString();
    if (layer.quantity) selector.setCount(`Measured in ${fmt(measures.count(layer.quantity))} cells · ${fmt(grid.known)} surveyed`);
    else selector.setCount(layer.kind === "all" ? "" : `${fmt(grid.known)} cells surveyed`);
  }

  // Hovering a cell on a layer: what is there, and who measured it when.
  const tip = $("map-tip");
  map.addEventListener("mousemove", (e) => {
    if (layer.kind === "all") { tip.hidden = true; return; }
    const pt = map.createSVGPoint();
    pt.x = e.clientX;
    pt.y = e.clientY;
    const p = pt.matrixTransform(map.getScreenCTM().inverse());
    const gx = Math.floor(p.x / CELL), gy = Math.floor(p.y / CELL);
    if (gx < 0 || gy < 0 || gx >= GW || gy >= GH) { tip.hidden = true; return; }
    const i = gy * GW + gx;
    const ci = grid.cls[i];
    const now = performance.now();
    const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
    const lines = [ci >= 0 ? `<b>${esc(Layers.CLASS_LABEL[Layers.CLASS_NAMES[ci]])}</b>` : "<b>Never seen</b>"];
    if (layer.quantity) {
      const m = measures.get(layer.quantity, i);
      lines.push(m
        ? `${esc(layer.label)}: ${esc(Layers.describe(layer, m.v))}<br><span class="muted">measured ${Layers.fmtAge(now - m.seen)} by ${esc(m.unit)}</span>`
        : `<span class="muted">${esc(layer.label)}: not measured here yet</span>`);
    } else if (ci >= 0) {
      lines.push(`<span class="muted">scanned ${Layers.fmtAge(now - grid.seen[i])} by ${esc(grid.scanner[i])}</span>`);
    }
    tip.innerHTML = lines.join("<br>");
    tip.hidden = false;
    const box = tip.parentElement.getBoundingClientRect();
    const x = e.clientX - box.left, y = e.clientY - box.top;
    tip.style.left = `${x + 14 + tip.offsetWidth > box.width ? x - 14 - tip.offsetWidth : x + 14}px`;
    tip.style.top = `${y + 14}px`;
  });
  map.addEventListener("mouseleave", () => { tip.hidden = true; });

  function unitDot(id) {
    let g = map.querySelector(`g[data-unit="${CSS.escape(id)}"]`);
    if (!g) {
      g = el("g", { "data-unit": id });
      g.appendChild(el("circle", { class: "pulse", r: 9 }));
      g.appendChild(el("circle", { class: "unit", r: 7 }));
      g.appendChild(el("text", { class: "unit-label", x: 12, y: 4 }, id));
      map.appendChild(g);
      g.querySelector(".pulse").animate([{ r: 8, opacity: 1 }, { r: 30, opacity: 0 }], { duration: 1200, iterations: 2 });
    }
    return g;
  }

  function fmtReadings(r) {
    return Object.entries(r).map(([k, v]) => `${k} ${v}`).join("  ·  ");
  }

  function render() {
    const tbody = $("units");
    tbody.innerHTML = "";
    if (units.size === 0) {
      tbody.innerHTML = '<tr class="empty"><td colspan="5">Nothing found yet. Waiting for a gateway…</td></tr>';
    }
    let verified = 0;
    for (const [id, u] of [...units.entries()].sort()) {
      if (u.verified) verified++;
      const tr = document.createElement("tr");
      const r = u.reading;
      const cells = [
        [id, "mono"],
        [r?.kind ?? u.fingerprint?.kind ?? "?", ""],
        [r?.zone ?? "?", ""],
        [r ? fmtReadings(r.readings) : "discovered, no reading yet", "mono"],
        u.reason ? [`✗ ${u.reason}`, "sig-bad"]
          : faulty.has(id) ? [`✓ signed, ⚠ flagged faulty: ${faulty.get(id)}`, "sig-warn"]
          : u.verified ? [`✓ ${r.gateway_id}`, "sig-ok"] : ["—", ""],
      ];
      for (const [text, cls] of cells) {
        const td = document.createElement("td");
        td.textContent = text;
        if (cls) td.className = cls;
        tr.appendChild(td);
      }
      tbody.appendChild(tr);

      if (r?.location) {
        const g = unitDot(id);
        g.setAttribute("transform", `translate(${r.location.x} ${r.location.y})`);
        const dot = g.querySelector(".unit");
        dot.setAttribute("class", `unit ${u.verified ? "ok" : "bad"}`);
        // On a data layer, show only machines that measure it, coloured by their latest value
        // (grey if the verifier flagged them: their numbers aren't trusted).
        const v = layer.quantity ? r.readings?.[layer.quantity] : undefined;
        g.style.display = layer.quantity && typeof v !== "number" ? "none" : "";
        dot.style.fill = typeof v !== "number" ? "" : faulty.has(id) ? "#898781" : Layers.colourOf(layer, v);
      }
    }
    $("stat-units").textContent = units.size;
    $("stat-verified").textContent = verified;
    $("stat-rejected").textContent = rejected;
  }

  function renderZones() {
    const tbody = $("zones");
    tbody.innerHTML = "";
    for (const z of [...zoneScores.values()].sort((a, b) => a.zone.localeCompare(b.zone))) {
      const tr = document.createElement("tr");
      const cells = [
        [z.zone, "mono"],
        [z.recommendation.replace("_", " "), `rec-${z.recommendation}`],
        [z.score ?? "—", "mono"],
        [`${Math.round(z.confidence * 100)}%`, "mono"],
        [z.verdict, ""],
      ];
      for (const [text, cls] of cells) {
        const td = document.createElement("td");
        td.textContent = text;
        if (cls) td.className = cls;
        tr.appendChild(td);
      }
      tbody.appendChild(tr);
    }
  }

  function feed(text, cls = "info") {
    const li = document.createElement("li");
    const t = document.createElement("time");
    t.textContent = new Date().toLocaleTimeString();
    const span = document.createElement("span");
    span.className = cls;
    span.textContent = text;
    li.append(t, span);
    const list = $("feed");
    list.prepend(li);
    while (list.children.length > 200) list.lastChild.remove();
  }

  drawZones();
  const conn = $("conn");
  const STATUS = { live: ["live", "pill-ok"], reconnecting: ["reconnecting…", "pill-wait"], offline: ["offline", "pill-bad"] };

  Uplink.start({
    onStatus(state) {
      const [text, cls] = STATUS[state];
      conn.textContent = text;
      conn.className = `pill ${cls}`;
    },
    onLog: feed,
    onDiscovered(d) {
      const fp = d.fingerprint ?? {};
      feed(`Discovered ${d.unit_id} via ${d.protocol} at ${d.gateway_id}: ${fp.kind} (${fp.evidence})`, "info");
      units.set(d.unit_id, { ...(units.get(d.unit_id) ?? {}), fingerprint: fp });
      render();
    },
    onReading({ unitId, msg, ok, reason }) {
      const u = units.get(unitId) ?? {};
      if (ok) {
        if (u.reason) feed(`${unitId}: signature valid again`, "ok");
        units.set(unitId, { ...u, reading: msg, verified: true, reason: null });
        const now = performance.now();
        if (msg.survey) grid.applySurvey(msg.survey, unitId, now);
        measures.addReading(msg, now, faulty.has(unitId));
        if (layer.kind !== "all") queueLayer();
      } else {
        rejected++;
        feed(`Rejected reading for ${unitId}: ${reason}`, "bad");
        // Keep the last good reading on screen but mark the unit as compromised.
        units.set(unitId, { ...u, verified: false, reason });
      }
      render();
    },
    onZoneScore({ zone, msg, ok, reason }) {
      if (!ok) { feed(`Rejected zone score for ${zone}: ${reason}`, "bad"); return; }
      zoneScores.set(zone, msg);
      const before = new Set(faulty.keys());
      faulty.clear();
      for (const z of zoneScores.values()) for (const f of z.flagged ?? []) faulty.set(f.unit_id, f.reason);
      for (const [id, why] of faulty) if (!before.has(id)) feed(`Verifier flagged ${id} as faulty: ${why}`, "bad");
      if (measures.purge(faulty).length) queueLayer();   // never paint from a flagged sensor
      renderZones();
      render();
    },
    onSnapshot(snap) {
      const now = performance.now();
      grid.applySnapshot(snap, now);
      measures.applySnapshot(snap.measures, now, faulty);
      queueLayer();
    },
  });

  selector = Layers.createSelector({
    mount: $("layers"),
    legend: $("layer-legend"),
    storageKey: "hollow.layer.operator",
    onChange(next) {
      layer = next;
      tip.hidden = true;
      renderLayer();
      render();
    },
  });
  setInterval(queueLayer, 1000);   // scan ages keep growing; counts stay current
})();
