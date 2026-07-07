#!/usr/bin/env python3
"""QA flag derivation (spec §5). Pure decision logic — no geometry here.

conflation/match.py does the geometric buffer-matching and produces, per
OSM way and per authoritative way, a small record of what matched what
and whether the way falls inside an official-agency boundary. This module
turns those records into the reviewable QA flags in §5:

    phantom_candidate  — OSM way inside an official park/forest boundary
                         with NO authoritative match. Down-ranks (feeds
                         −confidence) and queues for human review.
    coverage_gap       — authoritative trail with NO OSM match. A
                         completeness gap: REPORT ONLY, never fabricate
                         geometry (§5.2).
    name_mismatch      — matched pair whose names disagree → review.
    operator_mismatch  — matched pair whose operators disagree → review.

Keeping this pure (dicts in, flags out) makes the safety-critical
phantom rule unit-testable without a geometry stack.
"""
from __future__ import annotations

from typing import Any, Iterable


def _norm(s: Any) -> str:
    return " ".join(str(s or "").strip().lower().split())


def flags_for_osm_way(match: dict[str, Any]) -> list[str]:
    """Flags for one OSM way given its match record.

    Expected keys on `match`:
      matched (bool), inside_official_boundary (bool),
      osm_name, auth_name, osm_operator, auth_operator
    """
    out: list[str] = []
    matched = bool(match.get("matched", False))
    inside = bool(match.get("inside_official_boundary", False))

    # The safety-critical one: an unmatched way inside an agency boundary
    # that only publishes maintained trails is a likely phantom/social
    # trail. This is exactly the over-inclusion failure the whole
    # confidence system exists to catch (§4 preamble).
    if inside and not matched:
        out.append("phantom_candidate")

    if matched:
        if _norm(match.get("osm_name")) and _norm(match.get("auth_name")) \
                and _norm(match.get("osm_name")) != _norm(match.get("auth_name")):
            out.append("name_mismatch")
        if _norm(match.get("osm_operator")) and _norm(match.get("auth_operator")) \
                and _norm(match.get("osm_operator")) != _norm(match.get("auth_operator")):
            out.append("operator_mismatch")

    return out


def flags_for_authoritative_way(match: dict[str, Any]) -> list[str]:
    """coverage_gap when an authoritative trail has no OSM counterpart."""
    return [] if bool(match.get("matched", False)) else ["coverage_gap"]


def summarize(osm_matches: Iterable[dict[str, Any]],
              auth_matches: Iterable[dict[str, Any]]) -> dict[str, int]:
    """Aggregate counts for qa/reports — a quick health read on a region."""
    counts = {"phantom_candidate": 0, "coverage_gap": 0,
              "name_mismatch": 0, "operator_mismatch": 0,
              "osm_ways": 0, "authoritative_ways": 0, "matched_pairs": 0}
    for m in osm_matches:
        counts["osm_ways"] += 1
        if m.get("matched"):
            counts["matched_pairs"] += 1
        for f in flags_for_osm_way(m):
            counts[f] += 1
    for m in auth_matches:
        counts["authoritative_ways"] += 1
        for f in flags_for_authoritative_way(m):
            counts[f] += 1
    return counts
