"""Record everything the dashboards receive, so a run can be replayed with no broker at all.

    python -m tools.record --out dashboard/recordings/demo.jsonl.gz      # Ctrl+C to stop
    python -m tools.record --minutes 8

Then open http://localhost:8000/dashboard/city.html?replay=recordings/demo.jsonl.gz
(or the operator view, index.html?replay=...). This is the fallback for a venue with no
network, and what a static host (e.g. GitHub Pages) can serve on its own.

The recording keeps the original signed envelopes byte for byte, and its first line holds the
public keys they were signed with. The dashboards verify every signature again on replay, so a
recording that was edited afterwards shows the edited messages as rejected.

Format (gzip-compressed JSON lines):
    {"type": "header", "version": 1, "started": <epoch s>, "trusted": {signer: public key}}
    {"t": <s since started>, "topic": "...", "payload": "<exact message text>", "retain": false}

Map snapshots are large and only matter to a viewer who joins late, so one a minute is kept.
"""

import argparse
import gzip
import json
import sys
import threading
import time
from pathlib import Path

from hollow import config
from hollow.mqtt import connect, make_client

TOPICS = [("uplink/#", 0), ("demo/caption", 0)]
SNAPSHOT_EVERY_S = 60


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default="dashboard/recordings/demo.jsonl.gz")
    parser.add_argument("--minutes", type=float, default=0, help="stop after this long (0 = until Ctrl+C)")
    parser.add_argument("--until-stdin-closes", action="store_true", help=argparse.SUPPRESS)  # used by run.py
    args = parser.parse_args()

    out = Path(args.out)
    if not out.is_absolute():
        out = config.REPO_ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    trusted = json.loads(config.TRUSTED_KEYS_FILE.read_text(encoding="utf-8"))
    started = time.time()
    lock = threading.Lock()
    stop = threading.Event()
    counts = {"messages": 0, "last_snapshot": -SNAPSHOT_EVERY_S}

    tmp = out.with_suffix(out.suffix + ".part")
    fh = gzip.open(tmp, "wt", encoding="utf-8", compresslevel=6)
    fh.write(json.dumps({"type": "header", "version": 1, "started": round(started, 3), "trusted": trusted}) + "\n")

    def on_message(client, userdata, msg):
        topic = msg.topic
        if not (topic.startswith("uplink/") or topic == "demo/caption"):
            return  # the dev broker (amqtt) also delivers unrelated retained messages
        if topic.startswith("uplink/keys/"):
            return  # the dashboards trust keys/trusted.json, not keys announced on the broker
        t = time.time() - started
        if topic == "uplink/map/snapshot":
            if t - counts["last_snapshot"] < SNAPSHOT_EVERY_S:
                return
            counts["last_snapshot"] = t
        line = {"t": round(t, 3), "topic": topic, "payload": msg.payload.decode("utf-8", "replace"), "retain": bool(msg.retain)}
        with lock:
            if not fh.closed:
                fh.write(json.dumps(line, separators=(",", ":")) + "\n")
                counts["messages"] += 1

    client = make_client("recorder")
    client.on_connect = lambda c, u, f, rc, p: c.subscribe(TOPICS)
    client.on_message = on_message
    connect(client)
    client.loop_start()
    print(f"Recording to {out.relative_to(config.REPO_ROOT) if out.is_relative_to(config.REPO_ROOT) else out}", flush=True)

    if args.until_stdin_closes:
        threading.Thread(target=lambda: (sys.stdin.read(), stop.set()), daemon=True).start()
    try:
        last_report = time.time()
        while not stop.is_set():
            if args.minutes and time.time() - started >= args.minutes * 60:
                break
            if time.time() - last_report >= 60:
                print(f"{counts['messages']} messages recorded ({(time.time() - started) / 60:.0f} min)", flush=True)
                last_report = time.time()
            stop.wait(0.5)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
        with lock:
            fh.close()
        tmp.replace(out)
        size = out.stat().st_size / 1e6
        print(f"Saved {counts['messages']} messages, {(time.time() - started) / 60:.1f} min, {size:.1f} MB to {out.name}", flush=True)


if __name__ == "__main__":
    main()
