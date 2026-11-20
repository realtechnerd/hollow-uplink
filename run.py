"""Start the whole local demo with one command, and stop it all with Ctrl+C.

    python run.py                  # broker, Modbus devices, gateway, verifier, sensors, scouts, web server
    python run.py --demo           # ...plus the scripted demo (tools/demo.py), press Enter for each step
    python run.py --demo --auto    # ...the demo advances by itself (good for recording)
    python run.py --record         # ...and record everything to dashboard/recordings/ for offline replay
    python run.py --uno COM3       # ...and bridge the real Arduino field station (--uno fake imitates one)

On Windows use .venv\\Scripts\\python run.py; on macOS/Linux .venv/bin/python run.py.

Each service runs as its own process, exactly as in the seven-terminal setup in the README, so
nothing here is a shortcut: it only starts them in the right order and waits for each to be ready.
Every service's full output goes to logs/<name>.log; the console shows it too, prefixed by name.
Missing keys (gw-local, verifier, operator) are created on first run.
"""

import argparse
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LOGS = ROOT / "logs"
PY = sys.executable
GATEWAY = "gw-local"
KEYS = (GATEWAY, "verifier", "operator")
COLOURS = ["\033[36m", "\033[33m", "\033[35m", "\033[32m", "\033[34m", "\033[91m", "\033[96m", "\033[93m", "\033[95m"]
RESET = "\033[0m"


def port_open(port: int, host: str = "127.0.0.1") -> bool:
    with socket.socket() as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


class Stack:
    def __init__(self, show_http: bool, quiet: bool = False):
        self.quiet = quiet         # --demo: keep the console for the narration; services log to files
        self.procs: list[tuple[str, subprocess.Popen]] = []
        self.show_http = show_http
        self.width = 9
        self.stopping = False
        self.ready: dict[str, tuple[str, int, threading.Event]] = {}   # name -> (text, lines needed, event)
        self.seen: dict[str, int] = {}
        LOGS.mkdir(exist_ok=True)

    def start(self, name: str, *args: str, stdin=subprocess.DEVNULL, ready: str | None = None, count: int = 1) -> subprocess.Popen:
        """Start a service. With ready=, start() returns only once it has printed that text count times
        (watching its output rather than probing its port, which the broker would log as an error)."""
        if ready:
            self.ready[name] = (ready, count, threading.Event())
            self.seen[name] = 0
        env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
        proc = subprocess.Popen(
            [PY, *args], cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            stdin=stdin, text=True, encoding="utf-8", errors="replace", bufsize=1,
            start_new_session=sys.platform != "win32",   # POSIX: its own process group, stopped as one
        )
        colour = COLOURS[len(self.procs) % len(COLOURS)]
        self.procs.append((name, proc))
        threading.Thread(target=self._pump, args=(name, proc, colour), daemon=True).start()
        if ready:
            if not self.ready[name][2].wait(30) or proc.poll() is not None:
                raise SystemExit(f"{name} did not start. See logs/{name}.log.")
        return proc

    def _pump(self, name: str, proc: subprocess.Popen, colour: str) -> None:
        with open(LOGS / f"{name}.log", "w", encoding="utf-8") as log:
            for line in proc.stdout:
                log.write(line)
                log.flush()
                if name in self.ready and self.ready[name][0] in line:
                    self.seen[name] += 1
                    if self.seen[name] >= self.ready[name][1]:
                        self.ready[name][2].set()
                if not self.show_http and name == "web" and '"GET ' in line:
                    continue  # one line per file request is noise
                if self.quiet and not any(w in line for w in ("Traceback", "Error", "ERROR")):
                    continue
                print(f"{colour}{name:>{self.width}}{RESET} | {line.rstrip()}", flush=True)

    def check(self) -> str | None:
        """Name of a service that exited on its own, if any."""
        for name, proc in self.procs:
            if proc.poll() is not None:
                return name
        return None

    def stop(self) -> None:
        if self.stopping:
            return
        self.stopping = True
        for name, proc in self.procs:
            if name == "recorder" and proc.poll() is None:
                proc.stdin.close()   # tells the recorder to finish its file cleanly
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    pass
        for name, proc in reversed(self.procs):
            kill_tree(proc)
        for name, proc in self.procs:
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()


def kill_tree(proc: subprocess.Popen) -> None:
    """Stop a service and anything it started (the Modbus devices run in worker processes)."""
    if sys.platform == "win32":
        # Even after the venv launcher has exited, its workers may still be running.
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    elif proc.poll() is None:
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def ensure_keys() -> None:
    for key in KEYS:
        if not (ROOT / "keys" / f"{key}.key").exists():
            print(f"Creating signing key {key} (adds its public key to keys/trusted.json)")
            subprocess.run([PY, "-m", "tools.keygen", key], cwd=ROOT, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--scouts", type=int, default=3)
    parser.add_argument("--tick", type=float, default=0.3, help="seconds per scout move")
    parser.add_argument("--demo", action="store_true", help="run the scripted demo (tools/demo.py) alongside")
    parser.add_argument("--auto", action="store_true", help="with --demo: advance the steps automatically")
    parser.add_argument("--record", nargs="?", const="dashboard/recordings/demo.jsonl.gz", metavar="FILE",
                        help="record everything the dashboards see, for offline replay")
    parser.add_argument("--uno", metavar="PORT", help="also bridge the Arduino field station on this serial port "
                        "(e.g. COM3), or 'fake' to imitate one (see hardware/README.md)")
    parser.add_argument("--no-browser", action="store_true", help="don't open the 3D view")
    parser.add_argument("--show-http", action="store_true", help="also print the web server's request log")
    args = parser.parse_args()
    if sys.platform == "win32":
        os.system("")  # turn on ANSI colours in the Windows console

    busy = [p for p in (1883, 9001, 8000, 5020, 5021, 5022) if port_open(p)]
    if busy:
        raise SystemExit(
            f"Ports already in use: {', '.join(map(str, busy))}. Is the demo (or another broker) already "
            "running? Stop it first."
        )
    ensure_keys()

    stack = Stack(args.show_http, quiet=args.demo)
    director = None
    try:
        stack.start("broker", "dev/broker.py", ready="Dev broker up")
        if args.record:
            stack.start("recorder", "-m", "tools.record", "--out", args.record, "--until-stdin-closes",
                        stdin=subprocess.PIPE, ready="Recording to")
            time.sleep(0.5)  # subscribed before anything is published
        stack.start("modbus", "-m", "simulators.modbus_device", ready="on modbus tcp", count=3)
        stack.start("gateway", "-m", "gateway.gateway", "--gateway", GATEWAY)
        stack.start("verifier", "-m", "verifier.verifier")
        stack.start("sensors", "-m", "simulators.mqtt_sensor", "--gateway", GATEWAY, "--faulty", "mqtt-rad-02")
        stack.start("scouts", "-m", "simulators.scout", "--gateway", GATEWAY,
                    "--scouts", str(args.scouts), "--tick", str(args.tick))
        if args.uno:
            where = ["--fake"] if args.uno.lower() == "fake" else ["--port", args.uno]
            stack.start("uno", "-m", "tools.uno_bridge", "--gateway", GATEWAY, *where)
        stack.start("web", "dev/serve.py", ready="Serving")

        url = "http://localhost:8000/dashboard/city.html" + ("?captions=1" if args.demo else "")
        print(f"\n  Hollow Uplink is running.  3D view: {url}")
        print("  Operator view: http://localhost:8000/dashboard/   Logs: logs/   Stop: Ctrl+C\n", flush=True)
        if not args.no_browser:
            webbrowser.open(url)

        if args.demo:
            # The demo reads Enter from this console, so it shares our stdin and stdout directly.
            director = subprocess.Popen([PY, "-m", "tools.demo", "--gateway", GATEWAY, *(["--auto"] if args.auto else [])],
                                        cwd=ROOT, env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"})
        while True:
            time.sleep(0.5)
            dead = stack.check()
            if dead:
                print(f"\n{dead} stopped unexpectedly; see logs/{dead}.log. Shutting everything down.")
                break
            if director and director.poll() is not None:
                failed = director.returncode != 0
                director = None
                if failed:
                    print("\nThe scripted demo stopped with an error (above). Everything else is still running.")
                elif args.auto and args.record:
                    print("\nDemo finished; stopping (the recording is saved).")
                    break
    except KeyboardInterrupt:
        print("\nStopping…")
    finally:
        if director and director.poll() is None:
            director.terminate()
        stack.stop()
        print("Everything stopped.")


if __name__ == "__main__":
    main()
