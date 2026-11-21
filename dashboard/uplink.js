// Shared live-data client for every Hollow Uplink view (operator dashboard and 3D city).
// Connects to the broker, verifies every signed envelope against keys/trusted.json,
// rejects replays, and hands verified (or rejected) messages to the view.
//
// Who may sign what (mirrors hollow/signing.py):
//   readings, acks                   -> a gateway key
//   zone scores, investigations, map -> the "verifier" key
//   commands, kill switch            -> the "operator" key
//
// The operator's private key is never in the page source. A person unlocks operator mode
// by loading keys/operator.key; it stays in memory only, for this tab.
//
// Replay: with ?replay=<file> in the page URL, messages come from a recording made by
// tools/record.py instead of a broker (no network needed). Signatures, roles and replay
// protection are checked exactly as live, against the public keys stored in the recording,
// with the clock set to when each message was recorded. ?speed=2 plays twice as fast.
//
// Captions: the scripted demo (tools/demo.py) posts narration on demo/caption. It's unsigned
// presentation text, so it's only shown when the page asks for it (?captions=1) or in a replay.
//
// Needs mqtt.js (global `mqtt`), tweetnacl (global `nacl`) and config.js loaded first.
window.Uplink = (() => {
  const cfg = window.HOLLOW_CONFIG;
  const params = new URLSearchParams(location.search);
  const RECORDING = cfg.recordingUrl ?? "recordings/demo.jsonl.gz";
  // Public demo build (tools/build_public.py): never touch a broker, always play the recording.
  const replayUrl = params.get("replay") ?? (cfg.publicDemo ? RECORDING : null);
  const wantCaptions = replayUrl !== null || params.get("captions") === "1";
  let replayClock = null;           // ms since epoch of the message being replayed
  const now = () => replayClock ?? Date.now();
  const MAX_AGE_S = 120, MAX_SKEW_S = 120;
  let trusted = {};
  let client = null;
  let operator = null;              // { keyPair } once unlocked
  const boot = Math.random().toString(16).slice(2, 10);
  let seq = 0;

  // ---------- encoding ----------
  function b64ToBytes(b64) {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }
  function bytesToB64(bytes) {
    let bin = "";
    for (const b of bytes) bin += String.fromCharCode(b);
    return btoa(bin);
  }
  const nowIso = () => new Date(now()).toISOString().replace(/\.\d+Z$/, "Z");

  // ---------- trust ----------
  const SPECIAL = new Set(["verifier", "operator"]);
  const roleOf = (signer) => (SPECIAL.has(signer) ? signer : "gateway");

  // Mirrors hollow/signing.py open_envelope: verify the exact payload string, then parse.
  function openEnvelope(env, role) {
    const pub = trusted[env.gateway_id];
    if (!pub) throw new Error(`untrusted signer "${env.gateway_id}"`);
    if (role && roleOf(env.gateway_id) !== role) throw new Error(`"${env.gateway_id}" may not sign this kind of message`);
    let ok = false;
    try {
      ok = nacl.sign.detached.verify(
        new TextEncoder().encode(env.payload), b64ToBytes(env.signature), b64ToBytes(pub));
    } catch { ok = false; }
    if (!ok) throw new Error("bad signature");
    const msg = JSON.parse(env.payload);
    if (msg.gateway_id !== env.gateway_id) throw new Error("payload gateway_id does not match signer");
    return msg;
  }

  // Mirrors hollow/replay.py ReplayGuard. Returns false for a harmless re-send of a retained message.
  const lastSeen = new Map();
  function checkFresh(stream, msg, retained) {
    const age = (now() - Date.parse(msg.timestamp)) / 1000;
    if (Number.isNaN(age) || msg.boot == null || msg.seq == null) throw new Error("missing timestamp or sequence number");
    const prev = lastSeen.get(stream);
    if (prev && prev.boot === msg.boot && msg.seq === prev.seq && retained) return false;
    if (age > MAX_AGE_S) throw new Error(`stale: written ${Math.round(age)} s ago (possible replay)`);
    if (age < -MAX_SKEW_S) throw new Error("timestamp is in the future");
    if (prev && prev.boot === msg.boot && msg.seq <= prev.seq) throw new Error(`replayed: sequence ${msg.seq} already seen`);
    lastSeen.set(stream, { boot: msg.boot, seq: msg.seq });
    return true;
  }

  function parse(text) {
    try { return JSON.parse(text); } catch { return null; }
  }

  /**
   * handlers: {
   *   onStatus(state)                      "live" | "reconnecting" | "offline"
   *   onLog(text, cls)                     cls: "info" | "ok" | "bad"
   *   onReading({unitId, msg, ok, reason}) msg is null when rejected
   *   onDiscovered(discovery)
   *   onZoneScore({zone, msg, ok, reason})
   *   onSnapshot(snapshot)                 verified full map from the verifier (retained)
   *   onInvestigations(list)               verified list of recent investigations (retained)
   *   onAck(ack)                           a gateway accepted / rejected / finished a task (verified)
   *   onCommand({unitId, msg, ok, reason}) an operator command on the broker; ok=false if forged or replayed
   *   onKillswitch(state)                  verified kill switch state (retained)
   * }
   */
  async function start(h) {
    const log = h.onLog ?? (() => {});
    if (replayUrl !== null) return startReplay(h, log);
    try {
      trusted = await (await fetch(cfg.trustedKeysUrl, { cache: "no-store" })).json();
      log(`Loaded ${Object.keys(trusted).length} trusted key(s): ${Object.keys(trusted).join(", ") || "none"}`, "info");
    } catch {
      log(`Could not load trusted keys from ${cfg.trustedKeysUrl}; every reading will be rejected`, "bad");
    }

    client = mqtt.connect(cfg.brokerUrl, {
      username: cfg.username || undefined,
      password: cfg.password || undefined,
      reconnectPeriod: 2000,
    });
    let everConnected = false;
    client.on("connect", () => {
      everConnected = true;
      h.onStatus?.("live");
      client.subscribe([
        "uplink/readings/#", "uplink/discovered", "uplink/zones/+/score", "uplink/map/snapshot",
        "uplink/investigations", "uplink/acks/+", "uplink/control/killswitch", "uplink/commands/+",
        ...(wantCaptions ? ["demo/caption"] : []),
      ]);
    });
    client.on("reconnect", () => h.onStatus?.("reconnecting"));
    client.on("offline", () => h.onStatus?.("offline"));
    client.on("error", (err) => log(`Broker error: ${err.message}`, "bad"));
    client.on("message", (topic, payload, packet) => dispatch(h, log, topic, payload.toString(), !!packet?.retain));
    // No broker at all (e.g. the venue's network is down): offer the recorded run if there is one.
    setTimeout(async () => {
      if (everConnected || !h.onNoBroker) return;
      try {
        const res = await fetch(RECORDING, { method: "HEAD", cache: "no-store" });
        if (res.ok) h.onNoBroker(`?replay=${encodeURIComponent(RECORDING)}`);
      } catch { /* no recording either */ }
    }, 5000);
    return client;
  }

  function dispatch(h, log, topic, text, retained) {
    if (topic === "demo/caption") {
      if (wantCaptions) h.onCaption?.(text ? parse(text) : null);
      return;
    }
    const parts = topic.split("/");
    const data = parse(text);
    if (!data) return;

    // Verify a signed message from `role` on its own stream; null = ignore (duplicate retained).
    const accept = (role, stream) => {
      const msg = openEnvelope(data, role);
      return checkFresh(stream, msg, retained) ? msg : null;
    };

    try {
      if (topic === "uplink/discovered") {
        h.onDiscovered?.(data);
      } else if (parts[1] === "readings" && parts.length === 4) {
        const unitId = parts[3];
        try {
          const msg = accept("gateway", `reading/${unitId}`);
          if (!msg) return;
          if (msg.unit_id !== unitId) throw new Error("topic and payload unit_id differ");
          h.onReading?.({ unitId, msg, ok: true, reason: null });
        } catch (err) {
          h.onReading?.({ unitId, msg: null, ok: false, reason: err.message });
        }
      } else if (topic === "uplink/investigations") {
        const msg = accept("verifier", topic);
        if (msg) h.onInvestigations?.(msg.investigations ?? []);
      } else if (parts[1] === "acks") {
        const msg = accept("gateway", topic);
        if (msg) h.onAck?.(msg);
      } else if (parts[1] === "commands" && parts.length === 3) {
        // Commands are checked here too (the gateway checks them before acting), so a forged one is
        // visible on screen instead of only in the gateway's log.
        const unitId = parts[2];
        try {
          const msg = accept("operator", `command/${unitId}`);
          if (msg) h.onCommand?.({ unitId, msg, ok: true, reason: null });
        } catch (err) {
          h.onCommand?.({ unitId, msg: null, ok: false, reason: err.message });
        }
      } else if (topic === "uplink/control/killswitch") {
        const msg = accept("operator", topic);
        if (msg) h.onKillswitch?.(msg);
      } else if (topic === "uplink/map/snapshot") {
        const msg = accept("verifier", topic);
        if (msg) h.onSnapshot?.(msg);
      } else if (parts[1] === "zones" && parts[3] === "score") {
        const zone = parts[2];
        try {
          const msg = accept("verifier", topic);
          if (msg) h.onZoneScore?.({ zone, msg, ok: true, reason: null });
        } catch (err) {
          h.onZoneScore?.({ zone, msg: null, ok: false, reason: err.message });
        }
      }
    } catch (err) {
      log(`Rejected message on ${topic}: ${err.message}`, "bad");
    }
  }

  // ---------- replay ----------
  async function readRecording(url) {
    const res = await fetch(url, { cache: "no-store" });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    let bytes = new Uint8Array(await res.arrayBuffer());
    if (bytes[0] === 0x1f && bytes[1] === 0x8b) {   // gzip (some servers unpack it on the way already)
      const stream = new Blob([bytes]).stream().pipeThrough(new DecompressionStream("gzip"));
      bytes = new Uint8Array(await new Response(stream).arrayBuffer());
    }
    const lines = new TextDecoder().decode(bytes).split("\n").filter(Boolean);
    const header = JSON.parse(lines[0]);
    if (header.type !== "header") throw new Error("not a Hollow Uplink recording");
    return { header, records: lines.slice(1).map((l) => JSON.parse(l)) };
  }

  async function startReplay(h, log) {
    const speed = Math.max(0.25, Math.min(8, Number(params.get("speed")) || 1));
    const url = replayUrl || RECORDING;
    h.onStatus?.("loading");
    let rec;
    try {
      rec = await readRecording(url);
    } catch (err) {
      log(`Could not load the recording ${url}: ${err.message}`, "bad");
      h.onStatus?.("offline");
      return null;
    }
    trusted = rec.header.trusted ?? {};
    const length = rec.records.length ? rec.records[rec.records.length - 1].t : 0;
    log(`Replaying a recorded run (${Math.round(length / 60)} min, ${rec.records.length.toLocaleString()} messages` +
        `${speed !== 1 ? `, ${speed}× speed` : ""}). Every signature is checked again, against the keys it was recorded with.`, "info");
    h.onStatus?.("replay");
    const startedMs = rec.header.started * 1000;
    const t0 = performance.now();
    let i = 0;
    const pump = () => {
      const elapsed = ((performance.now() - t0) / 1000) * speed;
      while (i < rec.records.length && rec.records[i].t <= elapsed) {
        const r = rec.records[i++];
        replayClock = startedMs + r.t * 1000;
        dispatch(h, log, r.topic, r.payload, r.retain);
      }
      replayClock = startedMs + Math.min(elapsed, length) * 1000;
      if (i >= rec.records.length) {
        h.onStatus?.("ended");
        log("End of the recording. Reload the page to watch it again.", "info");
        return;
      }
      setTimeout(pump, 50);
    };
    pump();
    return null;
  }

  // ---------- operator mode ----------
  /** Unlock with the contents of keys/operator.key (base64 seed). Returns an error string or null. */
  function unlockOperator(seedB64) {
    try {
      const keyPair = nacl.sign.keyPair.fromSeed(b64ToBytes(seedB64.trim()));
      if (bytesToB64(keyPair.publicKey) !== trusted.operator) {
        return trusted.operator ? "That key isn't the trusted operator key." : "No operator key is trusted yet (run tools.keygen operator).";
      }
      operator = { keyPair };
      return null;
    } catch {
      return "That file doesn't contain a valid key.";
    }
  }
  const isOperator = () => operator !== null;
  function lockOperator() { operator = null; }

  function signAndPublish(topic, message, retain = false) {
    if (!client || !operator) return null;
    message.gateway_id = "operator";
    message.boot = boot;
    message.seq = ++seq;
    message.timestamp = nowIso();
    const payload = JSON.stringify(message);
    const signature = nacl.sign.detached(new TextEncoder().encode(payload), operator.keyPair.secretKey);
    client.publish(topic, JSON.stringify({ gateway_id: "operator", payload, signature: bytesToB64(signature) }), { retain });
    return message;
  }

  /** Approve a task for a machine. Gateways only act on operator-signed, "approved" commands. */
  function sendCommand({ unitId, type, params, taskId, sandbox = false }) {
    return signAndPublish(`uplink/commands/${unitId}`, {
      task_id: taskId ?? `t-${Date.now().toString(36)}`,
      unit_id: unitId,
      type,
      params,
      status: "approved",
      sandbox,
      requested_by: "dashboard",
      approved_by: "operator",
    });
  }

  function setKillswitch(engaged) {
    return signAndPublish("uplink/control/killswitch", { engaged, by: "operator" }, true);
  }

  return {
    start, sendCommand, setKillswitch, unlockOperator, lockOperator, isOperator, now,
    isReplay: replayUrl !== null,
    isPublicDemo: !!cfg.publicDemo,
  };
})();
