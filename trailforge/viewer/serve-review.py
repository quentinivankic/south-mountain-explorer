#!/usr/bin/env python3
"""Serve the deferred-review viewer locally (homelab-only, no auth).

Unlike serve.py (which serves the trailforge/ root for the assemble-AOI
viewer), this serves the REPO ROOT so review.html can fetch the published
per-area geom at /public/areas/geom/<slug>.json on demand — the review
manifest carries only slugs + flags, never geometry.

    python3 trailforge/viewer/serve-review.py
    # then open the printed URL

Regenerate the manifest first (after any republish) with:
    python3 trailforge/viewer/build-review.py
"""
import http.server
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]   # repo root (…/south-mountain-explorer)
PORT = int(os.environ.get("PORT", "8010"))


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")   # always fresh data
        super().end_headers()


if __name__ == "__main__":
    url = f"http://localhost:{PORT}/trailforge/viewer/review.html"
    print(f"deferred-review viewer: {url}")
    http.server.ThreadingHTTPServer(("", PORT), Handler).serve_forever()
