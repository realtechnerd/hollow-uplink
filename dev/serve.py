"""Static file server for local dev that tells the browser never to cache,
so edits to dashboard/*.js show up on a normal reload.

    python dev/serve.py            # http://localhost:8000/dashboard/city.html
"""

import functools
import http.server
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8000


BLOCKED_SUFFIXES = (".key", ".env")


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def send_head(self):
        # Never serve private signing keys or secrets, even locally.
        path = self.path.split("?", 1)[0].lower()
        if path.endswith(BLOCKED_SUFFIXES) or "/.env" in path or "/.git" in path:
            self.send_error(404)
            return None
        return super().send_head()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    handler = functools.partial(NoCacheHandler, directory=str(ROOT))
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler) as server:
        print(f"Serving {ROOT} at http://localhost:{PORT}/dashboard/city.html")
        server.serve_forever()
