#!/usr/bin/env python3
"""Serve the trailforge QA viewer locally (homelab-only, no auth).

Serves the trailforge/ root so the viewer can reach ../data and ../golden
with relative paths. Usage: `make qa` then open
http://localhost:8000/viewer/?aoi=sedona
"""
import http.server
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("PORT", "8000"))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")  # always fresh data
        super().end_headers()


if __name__ == "__main__":
    print(f"trailforge viewer: http://localhost:{PORT}/viewer/?aoi=sedona")
    http.server.ThreadingHTTPServer(("", PORT), Handler).serve_forever()
