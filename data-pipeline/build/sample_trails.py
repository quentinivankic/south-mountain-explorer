#!/usr/bin/env python3
"""Emit a small, diverse sample of a region's flagged trails (dev aid).

The full tile set is millions of features and lives only in the
`.pmtiles` artifact. For the on-device Trail Confidence Lab we want a
handful of REAL trails spanning the signal space — enough to tune the
weights against genuine data instead of synthetic fixtures.

This reads `<region>_trails_flagged.geojson` (Bucket A raw signals +
Bucket B flags, post-confidence.py), groups trails by their firing-signal
"signature", and emits one representative per signature (a named trail
where possible), most-prevalent first, capped at `--n`. Each record also
carries `count` (how many trails share that signature) so the sample
reflects the region's real distribution, plus the reference score/band.

Printed to the build log (readable without downloading the 127 MB tiles)
and written to `--out`. Purely a dev/authoring aid — no bearing on what
ships in the tiles.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import scoring_reference as sr

# The scoring-relevant fields the on-device lab consumes (mirror of the
# reference keys + TrailScoringProps). Everything else on the feature is
# irrelevant to the score and dropped from the sample.
FIELDS = (
    "name", "authoritative_match", "in_route_relation", "network",
    "has_known_operator", "has_name", "in_official_whitelist", "region_trust",
    "access", "informal", "lifecycle", "trail_visibility", "sac_scale",
    "tiger_unreviewed", "low_trust_editor", "osm_timestamp",
)


def _record(props: dict[str, Any]) -> dict[str, Any]:
    return {k: props.get(k) for k in FIELDS}


def sample(fc: dict[str, Any], n: int, weights: dict[str, Any],
           as_of: datetime) -> list[dict[str, Any]]:
    groups: dict[frozenset, dict[str, Any]] = {}
    for f in fc.get("features", []):
        props = f.get("properties", {}) or {}
        fired = frozenset(k for k, v in sr.active_signals(props, as_of=as_of).items() if v)
        g = groups.setdefault(fired, {"count": 0, "any": None, "named": None})
        g["count"] += 1
        rec = _record(props)
        if g["any"] is None:
            g["any"] = rec
        if g["named"] is None and rec.get("has_name") and (rec.get("name") or "").strip():
            g["named"] = rec

    out: list[dict[str, Any]] = []
    # Most-prevalent signatures first — that's what the region actually
    # looks like. Break ties by having a name (nicer samples).
    for _sig, g in sorted(groups.items(),
                          key=lambda kv: (-kv[1]["count"], kv[1]["named"] is None)):
        rec = dict(g["named"] or g["any"])
        s, b = sr.score_and_band(rec, weights, as_of=as_of)
        rec["count"] = g["count"]
        rec["score"] = round(s, 1)
        rec["band"] = b
        out.append(rec)
        if len(out) >= n:
            break
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sample flagged trails for the authoring lab")
    ap.add_argument("--trails", required=True, help="<region>_trails_flagged.geojson")
    ap.add_argument("--n", type=int, default=40)
    ap.add_argument("--out", required=True)
    # ISO timestamp for the edit-recency check; defaults to now.
    ap.add_argument("--as-of", default=None)
    args = ap.parse_args(argv)

    as_of = (datetime.fromisoformat(args.as_of.replace("Z", "+00:00"))
             if args.as_of else datetime.now(timezone.utc))
    with open(args.trails, encoding="utf-8") as fh:
        fc = json.load(fh)
    weights = sr.load_weights()
    samples = sample(fc, args.n, weights, as_of)

    total = len(fc.get("features", []))
    payload = {
        "total_trails": total,
        "distinct_signatures": len({
            frozenset(k for k, v in sr.active_signals(f.get("properties", {}) or {},
                                                      as_of=as_of).items() if v)
            for f in fc.get("features", [])
        }),
        "samples": samples,
    }
    Path(args.out).write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
