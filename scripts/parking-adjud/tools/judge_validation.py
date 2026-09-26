#!/usr/bin/env python3
"""Shared validation/discovery rules for parking judge drafts.

The checkpoint generator, review sheet, calibration ledger, and final store
merger must all read exactly the same canonical draft files and agree on what a
legal verdict row is. This module contains no writes.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

REQUIRED = {
    "fid", "area", "osm", "verdict", "prior", "exists", "public", "serves",
    "frames_used", "tags_cited", "confidence", "resolve_hint",
}
VERDICTS = ("KEEP", "DROP", "REVIEW")
CONFIDENCE = ("certain", "strong", "leaning")
CALLS = ("yes", "no", "unclear", "n/a")
FRAME_RE = re.compile(r"^z[123](?:_naip)?$")


def canonical_draft_files(tmp: str | Path, slug: str) -> list[Path]:
    """Return the one supported draft layout, rejecting ambiguous aliases.

    Legacy single-file drafts remain supported, but they may not coexist with
    numbered drafts. Numbered drafts must use exactly two digits and be a
    contiguous 00..NN sequence. Backup/alias JSON files are rejected instead of
    being silently consumed by one downstream tool and ignored by another.
    """
    root = Path(tmp)
    single = root / f"{slug}_verdict_draft.json"
    candidates = sorted(root.glob(f"{slug}_verdict_draft*.json"))
    numbered_re = re.compile(rf"^{re.escape(slug)}_verdict_draft_(\d{{2}})\.json$")
    numbered: list[tuple[int, Path]] = []
    unexpected: list[Path] = []
    for path in candidates:
        if path == single:
            continue
        match = numbered_re.match(path.name)
        if not match:
            unexpected.append(path)
        else:
            numbered.append((int(match.group(1)), path))
    if unexpected:
        raise ValueError("noncanonical verdict draft file(s): " + ", ".join(p.name for p in unexpected))
    if single.exists() and numbered:
        raise ValueError(f"{single.name} conflicts with numbered verdict drafts")
    if single.exists():
        return [single]
    numbered.sort()
    indexes = [index for index, _ in numbered]
    if indexes and indexes != list(range(len(indexes))):
        raise ValueError(f"numbered verdict drafts are not contiguous from 00: {indexes}")
    return [path for _, path in numbered]


def load_canonical_drafts(tmp: str | Path, slug: str) -> list[dict] | None:
    files = canonical_draft_files(tmp, slug)
    if not files:
        return None
    rows: list[dict] = []
    for path in files:
        value = json.loads(path.read_text())
        if not isinstance(value, list):
            raise ValueError(f"{path.name} must contain a JSON list")
        rows.extend(value)
    return rows


def original_judge_projection(row: dict) -> tuple[dict | None, list[str]]:
    """Return the pre-human-override row used for validation/calibration.

    `merge_drafts.py --set` deliberately leaves the judge's axis evidence in
    place and records the original verdict/confidence/hint under `override`.
    Restoring those three fields prevents a legal human override from looking
    like an axis/verdict inconsistency while still fingerprinting every piece of
    original judge evidence.
    """
    errors: list[str] = []
    if not isinstance(row, dict):
        return None, ["row is not an object"]
    projected = copy.deepcopy(row)
    override = projected.pop("override", None)
    if override is None:
        return projected, errors
    if not isinstance(override, dict):
        return None, ["override must be an object"]
    original_verdict = override.get("from")
    original_confidence = override.get("confidence_from")
    original_hint = override.get("resolve_hint")
    if original_verdict not in VERDICTS:
        errors.append(f"override.from is {original_verdict!r}")
    if original_confidence not in CONFIDENCE:
        errors.append(f"override.confidence_from is {original_confidence!r}")
    if original_verdict == "REVIEW" and not original_hint:
        errors.append("override restores REVIEW without resolve_hint")
    if original_verdict in ("KEEP", "DROP") and original_hint is not None:
        errors.append(f"override restores {original_verdict} with non-null resolve_hint")
    if row.get("verdict") == original_verdict:
        errors.append("override.from equals the current verdict")
    if errors:
        return None, errors
    projected["verdict"] = original_verdict
    projected["confidence"] = original_confidence
    projected["resolve_hint"] = original_hint
    return projected, errors


def validate_verdict_row(row: object, packet: dict | None = None,
                         allow_override: bool = True) -> tuple[list[str], dict | None]:
    """Validate one persisted row and return its original judge projection."""
    if not isinstance(row, dict):
        return ["row is not an object"], None
    errors: list[str] = []
    missing = sorted(REQUIRED - set(row))
    if missing:
        errors.append(f"missing keys {missing}")
    if "override" in row and not allow_override:
        errors.append("continuation rows may not contain human overrides")

    fid = row.get("fid")
    if not isinstance(fid, int):
        errors.append(f"fid is not an integer: {fid!r}")
    if not isinstance(row.get("area"), str) or not row.get("area"):
        errors.append("area must be a non-empty string")
    osm = row.get("osm")
    if not isinstance(osm, list) or not osm or any(not isinstance(x, str) or not x for x in osm):
        errors.append("osm must be a non-empty list of strings")
    if row.get("prior") not in ("surveyed", "bare"):
        errors.append(f"prior is {row.get('prior')!r}")
    if row.get("verdict") not in VERDICTS:
        errors.append(f"verdict is {row.get('verdict')!r}")
    if row.get("confidence") not in CONFIDENCE:
        errors.append(f"confidence is {row.get('confidence')!r}")
    current_hint = row.get("resolve_hint")
    if row.get("verdict") == "REVIEW" and not current_hint:
        errors.append("REVIEW without resolve_hint")
    if row.get("verdict") in ("KEEP", "DROP") and current_hint is not None:
        errors.append(f"{row.get('verdict')} with non-null resolve_hint")
    if not isinstance(row.get("tags_cited"), dict):
        errors.append("tags_cited must be an object")

    frames = row.get("frames_used")
    if not isinstance(frames, list) or not frames:
        errors.append("frames_used must be a non-empty list")
        frame_values: list[str] = []
    elif any(not isinstance(frame, str) for frame in frames):
        errors.append("frames_used entries must be strings")
        frame_values = []
    else:
        frame_values = frames
        if len(frames) != len(set(frames)):
            errors.append("frames_used contains duplicates")
        bad_frames = [frame for frame in frames if not FRAME_RE.fullmatch(frame)]
        if bad_frames:
            errors.append(f"invalid frames_used {bad_frames}")

    projection, projection_errors = original_judge_projection(row)
    errors.extend(projection_errors)
    source = projection if projection is not None else row
    calls: list[str] = []
    for axis in ("exists", "public", "serves"):
        value = source.get(axis)
        if not isinstance(value, dict) or set(value) != {"call", "evidence"}:
            errors.append(f"malformed {axis}")
            continue
        call = value.get("call")
        calls.append(call)
        if call not in CALLS:
            errors.append(f"{axis}.call is {call!r}")
        if not isinstance(value.get("evidence"), str) or not value["evidence"].strip():
            errors.append(f"{axis} has no evidence")

    original_verdict = source.get("verdict")
    if len(calls) == 3:
        if original_verdict == "KEEP" and calls != ["yes", "yes", "yes"]:
            errors.append("original KEEP does not have three yes axes")
        if original_verdict == "DROP" and "no" not in calls:
            errors.append("original DROP has no no axis")
        if original_verdict == "REVIEW" and ("no" in calls or "unclear" not in calls):
            errors.append("original REVIEW axes are inconsistent")
        if "no" in calls and original_verdict != "DROP":
            errors.append(f"original {original_verdict} has a no axis")
        if calls[0] == "no" and calls[1:] != ["n/a", "n/a"]:
            errors.append("EXISTS=no requires PUBLIC/SERVES=n/a")
        if calls[0] != "no" and "n/a" in calls:
            errors.append("n/a used without EXISTS=no")
    if (source.get("prior") == "surveyed" and original_verdict == "DROP"
            and not any(frame in ("z3", "z3_naip") for frame in frame_values)):
        errors.append("DROP of a surveyed prior without Z3 in frames_used")

    if packet is not None:
        for key in ("fid", "area", "osm", "prior"):
            if row.get(key) != packet.get(key):
                errors.append(f"{key} does not match the current packet")
    if row.get("coverage_gap") not in (None, True):
        errors.append("coverage_gap must be true when present")
    return errors, projection
