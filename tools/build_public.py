"""Build the public-facing demo: a static site that plays a recorded run and never talks to a broker.

    python -m tools.build_public                 # record a fresh run (about 5 min), then build public/
    python -m tools.build_public --reuse         # rebuild public/ from the last public recording

Nothing in the result touches our real devices or keys:

- The run is recorded with every machine simulated, the Arduino field station included
  (run.py --uno fake), so no real board is attached.
- It is signed with throwaway keys made just for this build (.public-demo/keys, git-ignored), not
  keys/*.key. The build checks that none of the recording's signers match keys/trusted.json.
- The site's config.js has no broker address and sets publicDemo, so the pages go straight to the
  recording. There is nothing to connect to and no operator mode.

public/ is only static files: upload it to any static host (GitHub Pages, Netlify, an S3 bucket).
Locally: python -m http.server -d public 8080, then open http://localhost:8080/.
"""

import argparse
import gzip
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WORK = ROOT / ".public-demo"
KEYS = WORK / "keys"
RECORDING = WORK / "demo.jsonl.gz"
SITE_FILES = ("city.html", "city.js", "city.css", "index.html", "app.js", "style.css", "uplink.js", "layers.js", "layers.css")

CONFIG_JS = """// Public demo build (tools/build_public.py). No broker: the pages play the recorded run only.
window.HOLLOW_CONFIG = {
  publicDemo: true,
  brokerUrl: "",
  username: "",
  password: "",
  trustedKeysUrl: "",
  recordingUrl: "recordings/demo.jsonl.gz",
};
"""

INDEX_REDIRECT = """<!doctype html>
<meta charset="utf-8">
<title>Hollow Uplink · public demo</title>
<meta http-equiv="refresh" content="0; url=city.html">
<a href="city.html">Open the Hollow Uplink demo</a>
"""


def record() -> None:
    if KEYS.exists():
        shutil.rmtree(KEYS)   # fresh throwaway keys every recording
    env = {**os.environ, "HOLLOW_KEYS_DIR": str(KEYS)}
    print("Recording a full scripted run with simulated machines and a simulated Uno (about 5 minutes)…", flush=True)
    subprocess.run([sys.executable, "run.py", "--demo", "--auto", "--uno", "fake", "--no-browser",
                    "--record", str(RECORDING)], cwd=ROOT, env=env, check=True)


def check_recording() -> dict:
    with gzip.open(RECORDING, "rt", encoding="utf-8") as fh:
        header = json.loads(fh.readline())
        topics = {json.loads(line)["topic"] for line in fh}
    real = json.loads((ROOT / "keys" / "trusted.json").read_text(encoding="utf-8"))
    clash = set(header["trusted"].values()) & set(real.values())
    if clash:
        raise SystemExit("The recording is signed with a real key from keys/trusted.json; refusing to publish it.")
    if not any(t.startswith("uplink/readings/") for t in topics):
        raise SystemExit("The recording has no readings; did the run fail? See logs/.")
    return {"signers": sorted(header["trusted"]), "uno": any("uno-" in t for t in topics), "topics": len(topics)}


def build(out: Path) -> None:
    if out.exists():
        shutil.rmtree(out)
    (out / "recordings").mkdir(parents=True)
    for name in SITE_FILES:
        shutil.copy2(ROOT / "dashboard" / name, out / name)
    (out / "config.js").write_text(CONFIG_JS, encoding="utf-8")
    (out / "index.html").rename(out / "operator.html")   # the flat operator view, still read-only here
    (out / "index.html").write_text(INDEX_REDIRECT, encoding="utf-8")
    for page in ("city.html", "city.js", "operator.html", "app.js"):
        p = out / page   # links between the two views
        p.write_text(p.read_text(encoding="utf-8").replace("index.html", "operator.html"), encoding="utf-8")
    shutil.copy2(RECORDING, out / "recordings" / "demo.jsonl.gz")
    (out / ".nojekyll").write_text("", encoding="utf-8")   # GitHub Pages: serve files as they are
    leaks = [p for p in out.rglob("*") if p.suffix in (".key", ".env")]
    if leaks:
        raise SystemExit(f"Refusing to build: private files in the site: {leaks}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reuse", action="store_true", help="skip recording; use the last public recording")
    parser.add_argument("--out", default="public", help="output folder (default: public/)")
    args = parser.parse_args()
    if not args.reuse or not RECORDING.exists():
        record()
    info = check_recording()
    out = (ROOT / args.out).resolve()
    build(out)
    print(f"\nPublic demo built in {out}")
    print(f"  signed by throwaway keys: {', '.join(info['signers'])}")
    print(f"  simulated Uno field station in the run: {'yes' if info['uno'] else 'NO'}")
    print(f"  try it: python -m http.server -d {args.out} 8080  ->  http://localhost:8080/")


if __name__ == "__main__":
    main()
