"""Static file server for local dev that tells the browser never to cache,
so edits to dashboard/*.js show up on a normal reload.

    python dev/serve.py            # http://localhost:8000/dashboard/city.html
"""

import functools
import http.server
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8000


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


if __name__ == "__main__":
    handler = functools.partial(NoCacheHandler, directory=str(ROOT))
    with http.server.ThreadingHTTPServer(("127.0.0.1", PORT), handler) as server:
        print(f"Serving {ROOT} at http://localhost:{PORT}/dashboard/city.html")
        server.serve_forever()
