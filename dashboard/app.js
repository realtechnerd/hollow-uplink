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
      map.appendChild(el("text", { class: "zone-label", x: z.x + 14, y: z.y + 26 }, z.id));
    }
  }

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
        g.querySelector(".unit").setAttribute("class", `unit ${u.verified ? "ok" : "bad"}`);
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
      renderZones();
      render();
    },
  });
})();
