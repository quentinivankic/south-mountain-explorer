#!/usr/bin/env python3
"""Serve the trailforge QA viewer on IPv4 loopback only (no auth).

Serves the trailforge/ root so the viewer can reach ../data and ../golden
with relative paths. Usage: `make qa` then open
http://127.0.0.1:8000/viewer/?aoi=sedona
"""
import http.server
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PORT = int(os.environ.get("PORT", "8000"))
BIND_HOST = "127.0.0.1"


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")  # always fresh data
        super().end_headers()


def create_server(bind_host: str = BIND_HOST):
    """Create the viewer server, rejecting every non-loopback bind request."""
    if bind_host != BIND_HOST:
        raise ValueError("the QA viewer only supports binding to 127.0.0.1")
    return http.server.ThreadingHTTPServer((BIND_HOST, PORT), Handler)


if __name__ == "__main__":
    print(f"trailforge viewer: http://127.0.0.1:{PORT}/viewer/?aoi=sedona")
    with create_server() as server:
        server.serve_forever()
