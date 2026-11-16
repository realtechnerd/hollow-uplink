// Shared live-data client for every Hollow Uplink view (operator dashboard and 3D city).
// Connects to the broker, verifies every signed envelope against keys/trusted.json,
// and hands verified (or rejected) messages to the view.
//
// Needs mqtt.js (global `mqtt`), tweetnacl (global `nacl`) and config.js loaded first.
window.Uplink = (() => {
  const cfg = window.HOLLOW_CONFIG;
  let trusted = {};

  function b64ToBytes(b64) {
    const bin = atob(b64);
    const out = new Uint8Array(bin.length);
    for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
    return out;
  }

  // Mirrors hollow/signing.py open_envelope: verify the exact payload string, then parse.
  function openEnvelope(env) {
    const pub = trusted[env.gateway_id];
    if (!pub) throw new Error(`untrusted signer "${env.gateway_id}"`);
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
   *   onAck(ack)                           a gateway accepted / rejected / finished a task
   * }
   */
  async function start(h) {
    const log = h.onLog ?? (() => {});
    try {
      trusted = await (await fetch(cfg.trustedKeysUrl, { cache: "no-store" })).json();
      log(`Loaded ${Object.keys(trusted).length} trusted key(s): ${Object.keys(trusted).join(", ") || "none"}`, "info");
    } catch {
      log(`Could not load trusted keys from ${cfg.trustedKeysUrl}; every reading will be rejected`, "bad");
    }

    const client = mqtt.connect(cfg.brokerUrl, {
      username: cfg.username || undefined,
      password: cfg.password || undefined,
      reconnectPeriod: 2000,
    });
    client.on("connect", () => {
      h.onStatus?.("live");
      client.subscribe([
        "uplink/readings/#", "uplink/discovered", "uplink/zones/+/score", "uplink/map/snapshot",
        "uplink/investigations", "uplink/acks/+",
      ]);
    });
    client.on("reconnect", () => h.onStatus?.("reconnecting"));
    client.on("offline", () => h.onStatus?.("offline"));
    client.on("error", (err) => log(`Broker error: ${err.message}`, "bad"));
    client.on("message", (topic, payload) => {
      const parts = topic.split("/");
      const data = parse(payload.toString());
      if (!data) return;

      if (topic === "uplink/discovered") {
        h.onDiscovered?.(data);
      } else if (parts[1] === "readings" && parts.length === 4) {
        const unitId = parts[3];
        try {
          const msg = openEnvelope(data);
          if (msg.unit_id !== unitId) throw new Error("topic and payload unit_id differ");
          h.onReading?.({ unitId, msg, ok: true, reason: null });
        } catch (err) {
          h.onReading?.({ unitId, msg: null, ok: false, reason: err.message });
        }
      } else if (topic === "uplink/investigations") {
        try {
          h.onInvestigations?.(openEnvelope(data).investigations ?? []);
        } catch (err) {
          log(`Rejected investigations update: ${err.message}`, "bad");
        }
      } else if (parts[1] === "acks") {
        h.onAck?.(data);
      } else if (topic === "uplink/map/snapshot") {
        try {
          h.onSnapshot?.(openEnvelope(data));
        } catch (err) {
          log(`Rejected map snapshot: ${err.message}`, "bad");
        }
      } else if (parts[1] === "zones" && parts[3] === "score") {
        const zone = parts[2];
        try {
          h.onZoneScore?.({ zone, msg: openEnvelope(data), ok: true, reason: null });
        } catch (err) {
          h.onZoneScore?.({ zone, msg: null, ok: false, reason: err.message });
        }
      }
    });
    activeClient = client;
    return client;
  }

  let activeClient = null;

  // Approve a task for a machine. Gateways only act on status "approved".
  function sendCommand({ unitId, type, params, taskId }) {
    if (!activeClient) return null;
    const cmd = {
      task_id: taskId ?? `t-${Date.now().toString(36)}`,
      unit_id: unitId,
      type,
      params,
      status: "approved",
      sandbox: false,
      requested_by: "dashboard",
      approved_by: "dashboard operator",
      timestamp: new Date().toISOString().replace(/\.\d+Z$/, "Z"),
    };
    activeClient.publish(`uplink/commands/${unitId}`, JSON.stringify(cmd));
    return cmd;
  }

  return { start, sendCommand };
})();
