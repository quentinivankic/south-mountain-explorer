#!/usr/bin/env python3
"""Reference implementation of the ON-DEVICE confidence score (spec §4.3).

IMPORTANT — read §4 first. This score is a *policy*, not data. It is NOT
baked into the tiles. The build ships raw signals (Bucket A) + a few
precomputed flags (Bucket B); the score is computed live on-device from a
tunable weights config so the developer can re-curate a region with no
tile rebuild. This file is the canonical, language-agnostic definition of
that computation:

  * It is the spec the Swift authoring build ports (§8 "authoring build").
  * It is what the unit tests pin, so a future weight change is a
    deliberate, reviewed edit — not an accidental drift.
  * It is a dev/authoring aid. It has NO place in the shipped user build,
    which carries no confidence UI at all (§8 "shipped build").

The score never removes a trail from the tiles (that is the licensing
gate's sole job, §2). It only informs which trails the *developer*
chooses to curate into the shipped set.

    score = clamp(base + Σ(weightᵢ × signalᵢ), 0, 100)
    band  = high (≥70) | medium (40–69) | low (<40)

`signalᵢ ∈ {0, 1}` — each weight is applied iff its signal fires. The
signal-firing rules come straight from the §4.3 "original mapping".
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

DEFAULT_WEIGHTS = Path(__file__).with_name("weights.default.json")

# OSM sac_scale ordinal ranks. The §4.3 flag is named `sac_scale_t4_plus`
# but its documented trigger is ">= demanding_mountain_hiking" (rank 3),
# so we threshold at rank 3 and keep the flag name for continuity.
_SAC_RANK = {
    "hiking": 1,
    "mountain_hiking": 2,
    "demanding_mountain_hiking": 3,
    "alpine_hiking": 4,
    "demanding_alpine_hiking": 5,
    "difficult_alpine_hiking": 6,
}
_SAC_T4_PLUS_THRESHOLD = 3  # demanding_mountain_hiking or harder

_NETWORK_NATIONAL = {"iwn", "nwn"}  # international / national walking network
_ACCESS_RESTRICTED = {"no", "private", "discouraged"}
_VISIBILITY_POOR = {"bad", "horrible", "no"}
_LIFECYCLE_DEAD = {"abandoned", "disused"}
_RECENT_EDIT_DAYS = 30


def load_weights(path: str | Path = DEFAULT_WEIGHTS) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as fh:
        return json.load(fh)


def _truthy(v: Any) -> bool:
    """Tile attributes may arrive as real bools or as OSM-ish strings."""
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in {"yes", "true", "1"}
    return bool(v)


def _sac_rank(v: Any) -> int:
    if v is None:
        return 0
    s = str(v).strip().lower()
    if s in _SAC_RANK:
        return _SAC_RANK[s]
    # tolerate "t3"/"T4" numeric-style encodings
    if s.startswith("t") and s[1:].isdigit():
        return int(s[1:])
    return 0


def _parse_ts(ts: Any) -> datetime | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):  # epoch seconds
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    s = str(ts).strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def active_signals(props: dict[str, Any], as_of: datetime | None = None) -> dict[str, bool]:
    """Map a trail's shipped attributes → the set of firing weight signals.

    `props` are the per-trail tile attributes: Bucket A raw signals
    (§4.1) + Bucket B precomputed flags (§4.2). `as_of` dates the
    edit-recency check; on-device this is "now". Passing it explicitly
    keeps the function pure + testable.
    """
    region_trust = str(props.get("region_trust", "")).strip().lower()

    recent = False
    ts = _parse_ts(props.get("osm_timestamp"))
    if ts is not None and as_of is not None:
        recent = (as_of - ts).days < _RECENT_EDIT_DAYS

    network = str(props.get("network", "")).strip().lower()

    return {
        # positives (Bucket B unless noted)
        "authoritative_match": _truthy(props.get("authoritative_match")),
        # GLOBAL "official" signal — hiking route-relation membership.
        # Works in every country's OSM with no per-country data source.
        "in_route_relation": _truthy(props.get("in_route_relation")),        # Bucket A
        # International / national walking network — the marquee routes.
        "network_national": network in _NETWORK_NATIONAL,
        "has_known_operator": _truthy(props.get("has_known_operator")),      # Bucket A
        "has_name": _truthy(props.get("has_name")),                          # Bucket A
        "in_official_whitelist": _truthy(props.get("in_official_whitelist")),
        "region_trust_high": region_trust == "high",
        # negatives
        "access_restricted": str(props.get("access", "")).strip().lower() in _ACCESS_RESTRICTED,
        "informal": _truthy(props.get("informal")),
        "lifecycle_abandoned_or_disused":
            str(props.get("lifecycle", "")).strip().lower() in _LIFECYCLE_DEAD,
        "trail_visibility_poor":
            str(props.get("trail_visibility", "")).strip().lower() in _VISIBILITY_POOR,
        "sac_scale_t4_plus": _sac_rank(props.get("sac_scale")) >= _SAC_T4_PLUS_THRESHOLD,
        "tiger_unreviewed": _truthy(props.get("tiger_unreviewed")),
        "recently_edited_or_low_trust":
            recent or _truthy(props.get("low_trust_editor")),
    }


def score(props: dict[str, Any], weights: dict[str, Any] | None = None,
          as_of: datetime | None = None) -> float:
    """Clamp(base + Σ weightᵢ·signalᵢ, 0, 100)."""
    w = weights or load_weights()
    base = float(w.get("base", 50))
    wmap = w.get("weights", {})
    total = base
    for name, fired in active_signals(props, as_of=as_of).items():
        if fired:
            total += float(wmap.get(name, 0))
    return max(0.0, min(100.0, total))


def band(value: float, weights: dict[str, Any] | None = None) -> str:
    w = weights or load_weights()
    bands = w.get("bands", {"high": 70, "medium": 40})
    if value >= bands.get("high", 70):
        return "high"
    if value >= bands.get("medium", 40):
        return "medium"
    return "low"


def score_and_band(props: dict[str, Any], weights: dict[str, Any] | None = None,
                   as_of: datetime | None = None) -> tuple[float, str]:
    w = weights or load_weights()
    s = score(props, w, as_of=as_of)
    return s, band(s, w)


if __name__ == "__main__":
    import sys
    w = load_weights()
    feats = json.load(sys.stdin).get("features", [])
    for f in feats:
        p = f.get("properties", {})
        s, b = score_and_band(p, w)
        print(f"{p.get('osm_id', '?'):>12}  {s:5.1f}  {b:<6}  {p.get('name') or '(unnamed)'}")
