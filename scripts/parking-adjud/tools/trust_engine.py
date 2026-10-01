#!/usr/bin/env python3
"""Pure trust-policy evaluation for Trekdex parking adjudication.

This module never reads or writes files.  It evaluates already-loaded source
verdicts, the human-review ledger, and labelled replay rows.  The policy is
shadow-only: it may identify a model class as eligible for direct automation,
but only after that exact class meets its asymmetric error bound across enough
areas and source families.  Everything else is routed to autonomous refresh,
blind challenge, or arbitration before a human is considered.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from typing import Iterable

from calibration import n_for_target, wilson_upper
from judge_validation import (
    LEGACY_OVERRIDE_VERSION,
    OVERRIDE_VERSION,
    authority_wrapper,
    original_judge_projection,
    validate_verdict_row,
)
import trust_resolution as resolution

REPORT_VERSION = 2
POLICY_VERSION = "parking-trust-shadow-v2"
PRIMARY_PROMOTION_IDENTITY_FIELDS = (
    "policy_id", "model_family", "model_id", "prompt_sha256",
)
UNAUTHENTICATED_PRIMARY_IDENTITY_DIMENSIONS = (
    "provider", "build", "normative_document_sha256",
)
KEEP_ERROR_TARGET = 0.01
DROP_ERROR_TARGET = 0.02
MIN_REVIEWED_AREAS = 3
MIN_SOURCE_FAMILIES = 2
GROUNDTRUTH_MATCH_M = 60.0

ROUTE_AUTHORITY = "PRESERVE_HUMAN_AUTHORITY"
ROUTE_RESOLVED = "PRESERVE_MACHINE_RESOLUTION"
ROUTE_AUTO_KEEP = "DIRECT_AUTO_KEEP"
ROUTE_AUTO_DROP = "DIRECT_AUTO_DROP"
ROUTE_REFRESH = "AUTONOMOUS_REFRESH"
ROUTE_CHALLENGE = "AUTONOMOUS_BLIND_CHALLENGE"
ROUTE_ARBITER = "AUTONOMOUS_ARBITER"
ROUTE_HUMAN = "HUMAN_EXCEPTION"

_WALK_RE = re.compile(
    r"\bwalk(?:_m|\s+(?:distance))?\s*(?:=|:)?\s*"
    r"([0-9][0-9,]*(?:\.[0-9]+)?)\s*(m|km|mi|miles?)\b",
    re.IGNORECASE,
)
_HUMAN_RE = re.compile(r"\b(?:user|human)\b", re.IGNORECASE)
_FALLBACK_NEGATED_RE = re.compile(
    r"\b(?:non[- ]?fallback|not\s+(?:a\s+)?fallback|no\s+fallback|"
    r"fallback\s*(?:[=:]\s*)?false)\b",
    re.IGNORECASE,
)
_FALLBACK_POSITIVE_RE = re.compile(
    r"\bfallback(?:-served|\s*(?:[=:]\s*)?true\b|"
    r"\s+(?:serve(?:d)?|edge|candidate|lot|range|within|at)\b)|"
    r"\bonly\s+(?:a\s+)?fallback\b",
    re.IGNORECASE,
)
_HARD_ACCESS = {"private", "no", "customers"}


def _counter(values: Iterable[str]) -> dict[str, int]:
    return dict(sorted(Counter(values).items()))


def _identity_key(value: object) -> str | None:
    """Canonical model/reviewer identity used for equality and breadth."""
    return resolution.identity_key(value)


def source_family(item: dict) -> str:
    """Stable coarse provenance family; filename fallbacks stay explicit."""
    src = str((item.get("published") or {}).get("src") or item.get("store") or "unknown")
    low = src.lower()
    if low.startswith("user"):
        return "human-authored"
    if low == "judge-fanout":
        return "judge-fanout"
    if low.startswith("opus"):
        return "opus"
    if "ne_verdicts" in low or low == "opus-ne":
        return "legacy-new-england"
    if "griffith" in low:
        return "legacy-griffith"
    return "unknown"


def _axis_evidence(row: dict) -> str:
    parts = []
    for axis in ("exists", "public", "serves"):
        value = row.get(axis)
        if isinstance(value, dict) and isinstance(value.get("evidence"), str):
            parts.append(value["evidence"])
    return " ".join(parts)


def _has_zoom(frames: object, zoom: str) -> bool:
    return isinstance(frames, list) and any(
        isinstance(frame, str) and frame in (zoom, f"{zoom}_naip") for frame in frames
    )


def _walk_metres(row: dict) -> float | None:
    serves = row.get("serves")
    evidence = serves.get("evidence") if isinstance(serves, dict) else None
    if not isinstance(evidence, str):
        return None
    match = _WALK_RE.search(evidence)
    if not match:
        return None
    value = float(match.group(1).replace(",", ""))
    unit = match.group(2).lower()
    if unit == "km":
        return value * 1000.0
    if unit in ("mi", "mile", "miles"):
        return value * 1609.344
    return value


def _positive_fallback(evidence: str) -> bool:
    """Recognize affirmative fallback evidence without treating negations as risk.

    Historical rows often spell out `non-fallback` or `fallback=false`; a plain
    substring test would incorrectly quarantine those ordinary near candidates.
    """
    return bool(_FALLBACK_POSITIVE_RE.search(evidence)) and not bool(
        _FALLBACK_NEGATED_RE.search(evidence)
    )


def decision_sha256(row: dict) -> str:
    """Stable identity of decision content, excluding provenance wrappers."""
    return resolution.decision_sha256(row)


def _primary_provenance_errors(original: dict) -> list[str]:
    provenance = original.get("judge_provenance")
    if not isinstance(provenance, dict):
        return ["primary judge_provenance is missing"]
    errors = []
    for field in ("model_id", "model_family"):
        if _identity_key(provenance.get(field)) is None:
            errors.append(f"primary {field} is missing, unknown, or noncanonical")
    for field in ("decision_sha256", "packet_sha256", "prompt_sha256", "evidence_sha256"):
        value = provenance.get(field)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            errors.append(f"primary {field} is not a lowercase SHA-256")
    if provenance.get("decision_sha256") != decision_sha256(original):
        errors.append("primary provenance decision hash does not match the judge call")
    return errors


def _primary_promotion_identity(original: object) -> dict | None:
    """Return the exact authenticated identity that may earn promotion credit.

    The shadow policy ID is host/code-owned. Current judge provenance binds the
    model family, model ID, and prompt hash, but does not bind provider, build,
    or normative-document hashes. Those unavailable dimensions are never
    represented by an ``unknown`` wildcard; callers must rotate one of the
    authenticated identity fields when any unavailable dimension changes.
    """
    if not isinstance(original, dict):
        return None
    provenance = original.get("judge_provenance")
    if not isinstance(provenance, dict):
        return None
    policy_id = _identity_key(POLICY_VERSION)
    model_family = _identity_key(provenance.get("model_family"))
    model_id = _identity_key(provenance.get("model_id"))
    prompt_sha256 = provenance.get("prompt_sha256")
    if (policy_id is None or model_family is None or model_id is None
            or not isinstance(prompt_sha256, str)
            or re.fullmatch(r"[0-9a-f]{64}", prompt_sha256) is None):
        return None
    return {
        "policy_id": policy_id,
        "model_family": model_family,
        "model_id": model_id,
        "prompt_sha256": prompt_sha256,
    }


def _promotion_identity_key(identity: object) -> tuple[str, str, str, str] | None:
    if not isinstance(identity, dict) or set(identity) != set(
            PRIMARY_PROMOTION_IDENTITY_FIELDS):
        return None
    values = tuple(identity.get(field) for field in PRIMARY_PROMOTION_IDENTITY_FIELDS)
    if not all(isinstance(value, str) for value in values):
        return None
    policy_id, model_family, model_id, prompt_sha256 = values
    if (_identity_key(policy_id) != policy_id
            or _identity_key(model_family) != model_family
            or _identity_key(model_id) != model_id
            or re.fullmatch(r"[0-9a-f]{64}", prompt_sha256) is None):
        return None
    return policy_id, model_family, model_id, prompt_sha256


def _promotion_reference(item: dict, area_record: dict, original: dict | None) -> dict:
    """Validate one per-decision blind independent reference label.

    Area-level booleans are deliberately insufficient. Promotion evidence binds
    both complete decisions plus primary/reviewer identities, distinct families
    and prompts, and the shared packet identity. Model challengers can resolve
    routine work, but only a human or external reference can calibrate direct
    model promotion.
    """
    result = {
        "valid": False,
        "reference_verdict": None,
        "reviewer_id": None,
        "reviewer_family": None,
        "error": False,
        "errors": [],
    }
    reviews = area_record.get("blind_reviews") or []
    if not reviews:
        return result
    if not isinstance(reviews, list):
        result["errors"].append("blind_reviews must be a list")
        return result
    fid = (original or {}).get("fid")
    matches = [row for row in reviews if isinstance(row, dict) and row.get("fid") == fid]
    if not matches:
        return result
    if len(matches) != 1:
        result["errors"].append("duplicate blind review identity")
        return result
    review = matches[0]
    required = {
        "fid", "reference_verdict", "reference_decision", "blind", "reviewer_id",
        "reviewer_family", "reviewer_kind", "primary_decision_sha256",
        "reviewer_decision_sha256", "packet_sha256", "prompt_sha256",
        "evidence_sha256",
    }
    missing = sorted(required - set(review))
    if missing:
        result["errors"].append(f"blind review missing keys {missing}")
    if review.get("blind") is not True:
        result["errors"].append("blind review is not marked blind")
    if review.get("reference_verdict") not in ("KEEP", "DROP"):
        result["errors"].append("blind review reference_verdict is not binary")
    if review.get("reviewer_kind") not in ("human", "external-reference"):
        result["errors"].append("reviewer_kind is not promotion-grade authority")

    reviewer_id = review.get("reviewer_id")
    reviewer_family = review.get("reviewer_family")
    reviewer_id_key = _identity_key(reviewer_id)
    reviewer_family_key = _identity_key(reviewer_family)
    if reviewer_id_key is None:
        result["errors"].append("reviewer_id is missing, unknown, or noncanonical")
    if reviewer_family_key is None:
        result["errors"].append("reviewer_family is missing, unknown, or noncanonical")

    primary = (original or {}).get("judge_provenance")
    if not isinstance(primary, dict):
        result["errors"].append("primary judge_provenance is missing")
        primary = {}
    primary_model_id_key = _identity_key(primary.get("model_id"))
    primary_family_key = _identity_key(primary.get("model_family"))
    if primary_model_id_key is None:
        result["errors"].append("primary model_id is missing, unknown, or noncanonical")
    if primary_family_key is None:
        result["errors"].append("primary model_family is missing, unknown, or noncanonical")
    for field in ("decision_sha256", "packet_sha256", "prompt_sha256", "evidence_sha256"):
        value = primary.get(field)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            result["errors"].append(f"primary {field} is not a lowercase SHA-256")
    if reviewer_id_key is not None and reviewer_id_key == primary_model_id_key:
        result["errors"].append("reviewer identity matches primary model")
    if reviewer_family_key is not None and reviewer_family_key == primary_family_key:
        result["errors"].append("reviewer family matches primary model family")
    if review.get("prompt_sha256") == primary.get("prompt_sha256"):
        result["errors"].append("reviewer prompt matches primary prompt")
    if review.get("packet_sha256") != primary.get("packet_sha256"):
        result["errors"].append("reviewer packet hash differs from the frozen primary packet")

    for field in (
        "primary_decision_sha256", "reviewer_decision_sha256", "packet_sha256",
        "prompt_sha256", "evidence_sha256",
    ):
        value = review.get(field)
        if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
            result["errors"].append(f"{field} is not a lowercase SHA-256")
    if isinstance(original, dict):
        expected = decision_sha256(original)
        if primary.get("decision_sha256") != expected:
            result["errors"].append("primary provenance decision hash does not match the judge call")
        if review.get("primary_decision_sha256") != expected:
            result["errors"].append("primary_decision_sha256 does not match the frozen judge call")

    reference = review.get("reference_decision")
    if not isinstance(reference, dict):
        result["errors"].append("reference_decision is not an object")
    else:
        reference_errors, _ = validate_verdict_row(reference, allow_override=False)
        if reference_errors:
            result["errors"].append("invalid reference_decision: " + "; ".join(reference_errors))
        for field in ("fid", "area", "osm", "prior"):
            if reference.get(field) != (original or {}).get(field):
                result["errors"].append(f"reference_decision {field} differs from primary")
        if reference.get("verdict") != review.get("reference_verdict"):
            result["errors"].append("reference_decision verdict differs from reference_verdict")
        if review.get("reviewer_decision_sha256") != decision_sha256(reference):
            result["errors"].append("reviewer_decision_sha256 does not match reference_decision")

    if result["errors"]:
        return result
    result.update({
        "valid": True,
        "reference_verdict": review["reference_verdict"],
        "reviewer_id": reviewer_id_key,
        "reviewer_family": reviewer_family_key,
        "error": review["reference_verdict"] != original.get("verdict"),
    })
    return result


def _review_status(item: dict, ledger_by_area: dict[str, dict], original: dict | None) -> dict:
    area = str(item.get("area") or "")
    rec = ledger_by_area.get(area) or {}
    verdict = (original or {}).get("verdict")
    mode = (rec.get("reviewed") or {}).get(verdict, "none")
    fid = (original or {}).get("fid")
    reviewed = mode == "each" or (
        mode == "sample" and isinstance(fid, int) and fid in set(rec.get("sample_fids") or [])
    )
    binary = reviewed and verdict in ("KEEP", "DROP")
    reference = _promotion_reference(item, rec, original)
    effective = item.get("row", {}).get("verdict")
    locally_valid_promotion = binary and reference["valid"]
    return {
        "mode": mode,
        "reviewed": reviewed,
        "binary": binary,
        "promotion_evidence_locally_valid": locally_valid_promotion,
        "promotion_error_locally_valid": (
            locally_valid_promotion and reference["error"]
        ),
        "promotion_evidence": locally_valid_promotion,
        "promotion_error": locally_valid_promotion and reference["error"],
        "promotion_reviewer_id": reference["reviewer_id"],
        "promotion_reviewer_family": reference["reviewer_family"],
        "promotion_reviewer_family_conflict": None,
        "promotion_reference_verdict": reference["reference_verdict"],
        "promotion_provenance_errors": reference["errors"],
        "deferred": reviewed and verdict == "REVIEW",
        "error": binary and effective != verdict,
    }


def analyse_item(item: dict, ledger_by_area: dict[str, dict]) -> dict:
    """Return deterministic evidence/risk facts for one canonical lot cluster."""
    row = item["row"]
    packet = item.get("packet") if isinstance(item.get("packet"), dict) else {}
    errors, projection = validate_verdict_row(row, packet or None)
    attestation = row.get("publication_attestation")
    publication_authority_verified = (
        isinstance(attestation, dict)
        and isinstance(attestation.get("attestation_sha256"), str)
        and item.get("published_authority_attestation_sha256")
        == attestation["attestation_sha256"]
    )
    if publication_authority_verified:
        errors = [
            error for error in errors
            if not error.startswith("cannot verify trust_resolution packet bytes")
            and not error.startswith("cannot verify human authority packet bytes")
        ]
    if projection is None:
        projection, projection_errors = original_judge_projection(row)
        errors = list(errors) + list(projection_errors)
    original = projection if isinstance(projection, dict) else row
    primary_provenance_errors = _primary_provenance_errors(original)
    primary_promotion_identity = _primary_promotion_identity(original)
    review = _review_status(item, ledger_by_area, original if isinstance(original, dict) else None)

    src = str((item.get("published") or {}).get("src") or item.get("store") or "")
    evidence = _axis_evidence(original)
    source_is_human = src.lower().startswith("user")
    evidence_is_human = bool(_HUMAN_RE.search(evidence))
    bound_authority = authority_wrapper(row)
    verified_receipt = False
    if bound_authority is not None:
        authority_kind, wrapper = bound_authority
        claimed_receipt = wrapper.get("authority_receipt_sha256")
        verified_receipt = (
            isinstance(claimed_receipt, str)
            and item.get("validated_authority_receipt_sha256") == claimed_receipt
        )
        if not verified_receipt:
            errors = list(errors) + [
                f"{authority_kind} authority receipt was not verified"
            ]
    override_errors = [error for error in errors if "override" in error]
    confirmation_errors = [error for error in errors if "human_confirmation" in error]
    override_value = row.get("override")
    receipt_bound_override = (
        isinstance(override_value, dict)
        and type(override_value.get("version")) is int
        and override_value.get("version") in (
            LEGACY_OVERRIDE_VERSION, OVERRIDE_VERSION
        )
    )
    override = (
        isinstance(override_value, dict)
        and not override_errors
        and (not receipt_bound_override or (verified_receipt and not errors))
    )
    confirmation = (
        isinstance(row.get("human_confirmation"), dict)
        and not confirmation_errors
        and verified_receipt
        and not errors
    )
    machine_resolved = isinstance(row.get("trust_resolution"), dict) and not errors
    explicit_label_authority = source_is_human or review["reviewed"] or override or confirmation
    human_influenced = explicit_label_authority or evidence_is_human

    frames = original.get("frames_used")
    calls = {
        axis: (original.get(axis) or {}).get("call")
        for axis in ("exists", "public", "serves")
    }
    packet_serves = packet.get("serves") if isinstance(packet.get("serves"), dict) else {}
    packet_walk = packet.get("walk") if isinstance(packet.get("walk"), dict) else {}
    structured_walk = packet_walk.get("walk_m")
    walk_m = (float(structured_walk) if isinstance(structured_walk, (int, float))
              else _walk_metres(original))
    fallback_signal = bool(packet_serves.get("fallback")) or _positive_fallback(evidence)
    connection = str(packet_walk.get("conn") or "").casefold()
    no_route_signal = (connection in ("no route", "no-route")
                       or "no route" in evidence.lower() or "no-route" in evidence.lower())
    tags = original.get("tags_cited") if isinstance(original.get("tags_cited"), dict) else {}
    hard_access = str(tags.get("access") or "").lower() in _HARD_ACCESS
    complete_ladder = all(_has_zoom(frames, zoom) for zoom in ("z1", "z2", "z3"))

    predecision_contaminated = source_is_human or evidence_is_human
    binary = original.get("verdict") in ("KEEP", "DROP")
    common = not errors and binary and not predecision_contaminated
    no_exception = not row.get("coverage_gap") and not fallback_signal and not no_route_signal

    strict = False
    balanced = False
    if common and original.get("verdict") == "KEEP":
        strict = (
            original.get("confidence") == "certain"
            and original.get("prior") == "surveyed"
            and complete_ladder
            and no_exception
            and walk_m is not None
            and walk_m <= 1609.344
        )
        balanced = (
            original.get("confidence") in ("certain", "strong")
            and complete_ladder
            and no_exception
            and walk_m is not None
            and walk_m <= 1609.344
        )
    elif common and original.get("verdict") == "DROP":
        exists_basis = (
            calls["exists"] == "no"
            and original.get("prior") == "bare"
            and _has_zoom(frames, "z3")
        )
        public_basis = calls["public"] == "no" and hard_access
        serves_basis = calls["serves"] == "no" and walk_m is not None and walk_m > 1609.344
        strict = (
            original.get("confidence") in ("certain", "strong")
            and no_exception
            and (exists_basis or public_basis or serves_basis)
        )
        balanced = (
            original.get("confidence") in ("certain", "strong")
            and no_exception
            and "no" in calls.values()
        )

    risk_signals = []
    if errors:
        risk_signals.append("schema-or-provenance-refresh")
    if original.get("verdict") == "REVIEW":
        risk_signals.append("original-review")
    if original.get("confidence") == "leaning":
        risk_signals.append("leaning")
    if row.get("coverage_gap"):
        risk_signals.append("coverage-gap")
    if fallback_signal:
        risk_signals.append("fallback")
    if no_route_signal:
        risk_signals.append("no-route")
    if walk_m is None:
        risk_signals.append("structured-walk-missing")
    if not complete_ladder:
        risk_signals.append("incomplete-ladder")
    if evidence_is_human:
        risk_signals.append("human-evidence-contamination")
    if review["promotion_provenance_errors"]:
        risk_signals.append("invalid-promotion-provenance")

    return {
        "key": item["key"],
        "source_key": item.get("source_key"),
        "store": item.get("store"),
        "area": item.get("area"),
        "source_family": source_family(item),
        "effective_verdict": row.get("verdict"),
        "original_verdict": original.get("verdict"),
        "original_confidence": original.get("confidence"),
        "prior": original.get("prior"),
        "validation_errors": sorted(set(errors)),
        "primary_provenance_valid": not primary_provenance_errors,
        "primary_provenance_errors": sorted(set(primary_provenance_errors)),
        "primary_promotion_identity": primary_promotion_identity,
        "calls": calls,
        "walk_m": round(walk_m, 3) if walk_m is not None else None,
        "complete_ladder": complete_ladder,
        "fallback_signal": fallback_signal,
        "no_route_signal": no_route_signal,
        "coverage_gap": bool(row.get("coverage_gap")),
        "source_is_human": source_is_human,
        "evidence_is_human": evidence_is_human,
        "override": override,
        "human_confirmation": confirmation,
        "machine_resolved": machine_resolved,
        "publication_authority_verified": publication_authority_verified,
        "explicit_label_authority": explicit_label_authority,
        "human_influenced": human_influenced,
        "review": review,
        "candidate": {"strict": strict, "balanced": balanced},
        "risk_signals": sorted(set(risk_signals)),
    }


def _invalidate_reviewer_family_conflicts(analyses: list[dict]) -> list[dict]:
    """Symmetrically remove promotion credit from relabelled reviewer IDs."""
    families_by_id: dict[str, set[str]] = defaultdict(set)
    rows_by_id: dict[str, list[dict]] = defaultdict(list)
    for analysis in analyses:
        review = analysis["review"]
        if review.get("promotion_evidence_locally_valid") is not True:
            continue
        reviewer_id = review.get("promotion_reviewer_id")
        reviewer_family = review.get("promotion_reviewer_family")
        if not isinstance(reviewer_id, str) or not isinstance(reviewer_family, str):
            continue
        families_by_id[reviewer_id].add(reviewer_family)
        rows_by_id[reviewer_id].append(analysis)

    conflicts = []
    for reviewer_id in sorted(families_by_id):
        families = sorted(families_by_id[reviewer_id])
        if len(families) < 2:
            continue
        affected = []
        for analysis in rows_by_id[reviewer_id]:
            review = analysis["review"]
            review["promotion_evidence"] = False
            review["promotion_error"] = False
            review["promotion_reviewer_family_conflict"] = families
            analysis["risk_signals"] = sorted(set(
                analysis["risk_signals"] + ["promotion-reviewer-family-conflict"]
            ))
            affected.append({
                "key": str(analysis["key"]),
                "area": str(analysis["area"]),
                "original_verdict": analysis["original_verdict"],
                "claimed_family": review["promotion_reviewer_family"],
            })
        conflicts.append({
            "reviewer_id": reviewer_id,
            "claimed_families": families,
            "affected_records": sorted(
                affected,
                key=lambda row: (
                    row["area"], row["key"], row["original_verdict"],
                    row["claimed_family"],
                ),
            ),
        })
    return conflicts


def _promotion_scope_metrics(identity: dict, eligible: list[dict], target: float,
                             min_areas: int, min_sources: int) -> dict:
    reviewed = [row for row in eligible if row["review"]["binary"]]
    historical_errors = sum(1 for row in reviewed if row["review"]["error"])
    promotion_rows = [
        row for row in eligible if row["review"]["promotion_evidence"]
    ]
    promotion_errors = sum(
        1 for row in promotion_rows if row["review"]["promotion_error"]
    )
    areas = sorted({str(row["area"]) for row in promotion_rows})
    reviewer_families = sorted({
        row["review"]["promotion_reviewer_family"] for row in promotion_rows
    })
    promotion_upper = wilson_upper(promotion_errors, len(promotion_rows))
    return {
        "primary_identity": dict(identity),
        "eligible": len(eligible),
        "reviewed": len(reviewed),
        "errors": historical_errors,
        "wilson_error_upper_95": wilson_upper(historical_errors, len(reviewed)),
        "promotion_reviewed": len(promotion_rows),
        "promotion_errors": promotion_errors,
        "promotion_error_upper_95": promotion_upper,
        "promotion_reviewed_areas": areas,
        "promotion_reviewed_reviewer_families": reviewer_families,
        "promotion_ready": (
            bool(promotion_rows)
            and promotion_upper <= target
            and len(areas) >= min_areas
            and len(reviewer_families) >= min_sources
        ),
    }


def _class_metrics(analyses: list[dict], policy_name: str, verdict: str,
                   target: float, min_areas: int, min_sources: int) -> dict:
    eligible = [a for a in analyses if a["candidate"][policy_name]
                and a["original_verdict"] == verdict]
    reviewed = [a for a in eligible if a["review"]["binary"]]
    historical_errors = sum(1 for a in reviewed if a["review"]["error"])
    promotion_rows = [a for a in eligible if a["review"]["promotion_evidence"]]
    promotion_errors = sum(1 for a in promotion_rows if a["review"]["promotion_error"])
    areas = sorted({str(a["area"]) for a in promotion_rows})
    sources = sorted({a["review"]["promotion_reviewer_family"] for a in promotion_rows})
    historical_upper = wilson_upper(historical_errors, len(reviewed))
    promotion_upper = wilson_upper(promotion_errors, len(promotion_rows))

    eligible_by_identity: dict[tuple[str, str, str, str], list[dict]] = defaultdict(list)
    identity_by_key: dict[tuple[str, str, str, str], dict] = {}
    for analysis in eligible:
        identity = analysis.get("primary_promotion_identity")
        identity_key = _promotion_identity_key(identity)
        if identity_key is None:
            continue
        eligible_by_identity[identity_key].append(analysis)
        identity_by_key[identity_key] = identity
    identity_metrics = [
        _promotion_scope_metrics(
            identity_by_key[identity_key], eligible_by_identity[identity_key],
            target, min_areas, min_sources,
        )
        for identity_key in sorted(eligible_by_identity)
    ]

    return {
        "eligible": len(eligible),
        "reviewed": len(reviewed),
        "errors": historical_errors,
        "observed_error_rate": historical_errors / len(reviewed) if reviewed else None,
        "wilson_error_upper_95": historical_upper,
        "review_note": (
            "Aggregate diagnostics never authorize routing; only one exact "
            "primary_identity entry may do so."
        ),
        "promotion_reviewed": len(promotion_rows),
        "promotion_errors": promotion_errors,
        "promotion_error_upper_95": promotion_upper,
        "target": target,
        "promotion_reviewed_areas": areas,
        "promotion_reviewed_source_families": sources,
        "minimum_reviewed_areas": min_areas,
        "minimum_reviewer_families": min_sources,
        "zero_error_reviews_needed_for_target": n_for_target(target),
        "promotion_authority_scope": "exact-primary-identity-only",
        "promotion_identity_metrics": identity_metrics,
        "ready_identity_count": sum(
            1 for metric in identity_metrics if metric["promotion_ready"]
        ),
    }


def candidate_policy_metrics(analyses: list[dict], policy_name: str,
                             keep_target: float = KEEP_ERROR_TARGET,
                             drop_target: float = DROP_ERROR_TARGET,
                             min_areas: int = MIN_REVIEWED_AREAS,
                             min_sources: int = MIN_SOURCE_FAMILIES) -> dict:
    return {
        "KEEP": _class_metrics(analyses, policy_name, "KEEP", keep_target,
                               min_areas, min_sources),
        "DROP": _class_metrics(analyses, policy_name, "DROP", drop_target,
                               min_areas, min_sources),
    }


def _identity_is_promotion_ready(metric: object, identity: object) -> bool:
    identity_key = _promotion_identity_key(identity)
    if identity_key is None or not isinstance(metric, dict):
        return False
    scopes = metric.get("promotion_identity_metrics")
    if not isinstance(scopes, list):
        return False
    return any(
        isinstance(scope, dict)
        and _promotion_identity_key(scope.get("primary_identity")) == identity_key
        and scope.get("promotion_ready") is True
        for scope in scopes
    )


def _route(analysis: dict, strict_metrics: dict) -> str:
    if analysis["explicit_label_authority"]:
        return ROUTE_AUTHORITY
    if analysis["machine_resolved"]:
        return ROUTE_RESOLVED
    verdict = analysis["original_verdict"]
    if analysis["validation_errors"]:
        return ROUTE_REFRESH
    exception_sensitive = (
        verdict == "REVIEW"
        or analysis["original_confidence"] == "leaning"
        or analysis["coverage_gap"]
        or analysis["fallback_signal"]
        or analysis["no_route_signal"]
    )
    if exception_sensitive:
        return ROUTE_ARBITER
    if (analysis["primary_provenance_valid"] and analysis["candidate"]["strict"]
            and verdict in ("KEEP", "DROP")):
        metric = strict_metrics.get(verdict) if isinstance(strict_metrics, dict) else None
        if _identity_is_promotion_ready(
                metric, analysis.get("primary_promotion_identity")):
            return ROUTE_AUTO_KEEP if verdict == "KEEP" else ROUTE_AUTO_DROP
    return ROUTE_CHALLENGE


def _binary_review_summary(analyses: list[dict], verdict: str) -> dict:
    rows = [a for a in analyses if a["original_verdict"] == verdict and a["review"]["binary"]]
    errors = sum(1 for a in rows if a["review"]["error"])
    return {
        "reviewed": len(rows),
        "errors": errors,
        "observed_error_rate": errors / len(rows) if rows else None,
        "wilson_error_upper_95": wilson_upper(errors, len(rows)),
        "areas": sorted({str(a["area"]) for a in rows}),
        "source_families": sorted({a["source_family"] for a in rows}),
    }


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def replay_groundtruth(sidecar: dict, groundtruth: dict) -> dict:
    """Coordinate-match historical labels; preserve honest provenance labels."""
    lots = sidecar.get("lots") or {}
    out = {}
    area_filters = {
        "zion": "zion-wilderness-ut",
        "griffith": "griffith-park-ca",
    }
    for group, labels in sorted((groundtruth or {}).items()):
        candidates = [
            (key, row) for key, row in lots.items()
            if row.get("area") == area_filters.get(group, row.get("area"))
        ]
        matched = []
        missing = []
        used_lots: set[str] = set()
        for label_key, label in sorted(labels.items()):
            nearest = None
            nearest_m = GROUNDTRUTH_MATCH_M
            for key, row in candidates:
                if key in used_lots:
                    continue
                distance = _haversine_m(label["lat"], label["lon"], row["lat"], row["lon"])
                if distance < nearest_m:
                    nearest_m = distance
                    nearest = (key, row)
            if nearest is None:
                missing.append(label_key)
                continue
            key, row = nearest
            used_lots.add(key)
            got = row.get("verdict")
            want = label.get("want")
            matched.append({
                "label": label_key,
                "lot": key,
                "want": want,
                "got": got,
                "agree": got == want,
                "distance_m": round(nearest_m, 3),
                "confidence": label.get("confidence"),
            })
        human = bool(labels) and all(str(x.get("src") or "").lower() == "user"
                                     for x in labels.values())
        out[group] = {
            "label_authority": "human" if human else "historical-model",
            "independent_holdout": False,
            "total_labels": len(labels),
            "matched_predictions": len(matched),
            "agreements": sum(1 for row in matched if row["agree"]),
            "disagreements": sum(1 for row in matched if not row["agree"]),
            "missing_predictions": len(missing),
            "missing_label_keys": missing,
            "note": (
                "Human labels are in-sample and encoded in current evidence; regression only."
                if human else
                "Labels were produced by an earlier model; self-consistency only, not human truth."
            ),
            "matches": matched,
        }
    return out


def build_report(items: list[dict], ledger: dict, groundtruth: dict,
                 sidecar: dict, corpus: dict, include_items: bool = False) -> dict:
    ledger_by_area = {row["area"]: row for row in ledger.get("areas", [])}
    analyses = [analyse_item(item, ledger_by_area) for item in items]
    reviewer_identity_conflicts = _invalidate_reviewer_family_conflicts(analyses)
    strict = candidate_policy_metrics(analyses, "strict")
    balanced = candidate_policy_metrics(analyses, "balanced")
    ready_exact_identities = [
        {"verdict": verdict, **metric["primary_identity"]}
        for verdict in ("KEEP", "DROP")
        for metric in strict[verdict]["promotion_identity_metrics"]
        if metric["promotion_ready"]
    ]
    for analysis in analyses:
        analysis["route"] = _route(analysis, strict)

    routes = _counter(a["route"] for a in analyses)
    routes.setdefault(ROUTE_RESOLVED, 0)
    routes.setdefault(ROUTE_HUMAN, 0)
    final_counts = _counter(a["effective_verdict"] for a in analyses)
    original_counts = _counter(a["original_verdict"] for a in analyses)
    schema_pass = sum(1 for a in analyses if not a["validation_errors"])
    errors = Counter(error for a in analyses for error in a["validation_errors"])
    authority = [a for a in analyses if a["explicit_label_authority"]]
    influenced = [a for a in analyses if a["human_influenced"]]
    direct_human = [a for a in analyses if a["source_is_human"]]
    ledger_reviewed = [a for a in analyses if a["review"]["reviewed"]]
    overrides = [a for a in analyses if a["override"]]
    deferred = [a for a in analyses if a["review"]["deferred"]]

    binary = {
        verdict: _binary_review_summary(analyses, verdict)
        for verdict in ("KEEP", "DROP")
    }
    reviewed_binary = [a for a in analyses if a["review"]["binary"]]
    reviewed_areas = sorted({str(a["area"]) for a in reviewed_binary})
    reviewed_sources = sorted({a["source_family"] for a in reviewed_binary})
    promotion_binary = [a for a in analyses if a["review"]["promotion_evidence"]]
    promotion_areas = sorted({str(a["area"]) for a in promotion_binary})
    promotion_sources = sorted({a["review"]["promotion_reviewer_family"] for a in promotion_binary})
    area_counts = Counter(str(a["area"]) for a in analyses)
    source_counts = Counter(a["source_family"] for a in analyses)
    route_by_verdict = {
        route: _counter(a["original_verdict"] for a in analyses if a["route"] == route)
        for route in sorted(routes)
    }
    route_by_confidence = {
        route: _counter(a["original_confidence"] for a in analyses if a["route"] == route)
        for route in sorted(routes)
    }
    route_by_source = {
        route: _counter(a["source_family"] for a in analyses if a["route"] == route)
        for route in sorted(routes)
    }
    risk_signal_counts = _counter(signal for a in analyses for signal in a["risk_signals"])

    report = {
        "version": REPORT_VERSION,
        "policy": {
            "id": POLICY_VERSION,
            "shadow_only": True,
            "same_model_votes_count_as_independent": False,
            "uncertainty_action": "autonomous challenge/arbitration; no direct user route",
            "direct_model_promotion": {
                "KEEP": {"error_upper_95_target": KEEP_ERROR_TARGET},
                "DROP": {"error_upper_95_target": DROP_ERROR_TARGET},
                "minimum_reviewed_areas": MIN_REVIEWED_AREAS,
                "minimum_reviewer_families": MIN_SOURCE_FAMILIES,
                "authority_scope": "exact-primary-identity-only",
                "primary_identity_fields": list(PRIMARY_PROMOTION_IDENTITY_FIELDS),
                "unauthenticated_primary_identity_dimensions": list(
                    UNAUTHENTICATED_PRIMARY_IDENTITY_DIMENSIONS
                ),
                "unauthenticated_dimension_contract": (
                    "Current judge_provenance does not authenticate provider, build, or "
                    "normative-document hashes. They are not accepted as unknown or wildcard "
                    "identity values; any change to one must rotate model_id or prompt_sha256 "
                    "before promotion credit can transfer."
                ),
                "reference_contract": (
                    "one blind_reviews record per fid with promotion-grade reviewer identity, "
                    "a family different from the primary, frozen primary/reviewer decisions, "
                    "and packet/prompt/evidence SHA-256 identities"
                ),
            },
        },
        "corpus": {
            **corpus,
            "final_verdicts": final_counts,
            "original_judge_verdicts": original_counts,
            "schema_current": schema_pass,
            "schema_not_current": len(analyses) - schema_pass,
            "schema_error_counts": dict(sorted(errors.items())),
            "areas": dict(sorted(area_counts.items())),
            "source_families": dict(sorted(source_counts.items())),
            "largest_area_share": max(area_counts.values()) / len(analyses) if analyses else 0.0,
            "largest_source_share": max(source_counts.values()) / len(analyses) if analyses else 0.0,
        },
        "provenance": {
            "explicit_label_authority": len(authority),
            "direct_human_source": len(direct_human),
            "ledger_reviewed_per_lot": len(ledger_reviewed),
            "structured_overrides": len(overrides),
            "human_influenced_union": len(influenced),
            "model_only_or_unreviewed": len(analyses) - len(influenced),
            "promotion_reviewer_identity_conflicts": reviewer_identity_conflicts,
        },
        "routing": {
            "counts": routes,
            "by_original_verdict": route_by_verdict,
            "by_original_confidence": route_by_confidence,
            "by_source_family": route_by_source,
            "risk_signal_counts": risk_signal_counts,
            "total": len(analyses),
            "direct_user_work_created": routes.get(ROUTE_HUMAN, 0),
            "model_direct_auto": routes.get(ROUTE_AUTO_KEEP, 0) + routes.get(ROUTE_AUTO_DROP, 0),
            "autonomous_followup": (
                routes.get(ROUTE_REFRESH, 0)
                + routes.get(ROUTE_CHALLENGE, 0)
                + routes.get(ROUTE_ARBITER, 0)
            ),
        },
        "candidate_policies": {"strict": strict, "balanced": balanced},
        "calibration": {
            "binary": binary,
            "deferred_reviews_resolved": len(deferred),
            "deferred_resolution_counts": _counter(a["effective_verdict"] for a in deferred),
        },
        "generalization": {
            "ready_exact_primary_identities": ready_exact_identities,
            "historical_binary_reviewed_areas": reviewed_areas,
            "historical_binary_reviewed_source_families": reviewed_sources,
            "promotion_grade_reviewed": len(promotion_binary),
            "promotion_reviewed_areas": promotion_areas,
            "promotion_reviewed_source_families": promotion_sources,
            "area_holdout_available": len(promotion_areas) >= MIN_REVIEWED_AREAS,
            "source_holdout_available": len(promotion_sources) >= MIN_SOURCE_FAMILIES,
            "note": (
                "Historical model-visible review is diagnostic only. No direct model class "
                "may be promoted until blind independent evidence, its own error bound, and "
                "cross-area/source breadth all pass; in-sample labels never satisfy this gate."
            ),
        },
        "historical_replay": replay_groundtruth(sidecar, groundtruth),
        "limitations": [
            f"The {len(analyses):,} final verdicts are not {len(analyses):,} independent ground-truth labels.",
            "Confidence is self-reported and is never treated as calibrated probability.",
            "Same-model fan-out shares prompts, evidence, imagery, and systematic errors.",
            "Colorado structured dossier/serve/walk inputs are not committed; prose parsing is audit-only.",
            "Historical Zion human labels are in-sample; Griffith labels are model-authored.",
            "A blind challenger and arbiter must preserve model/prompt/evidence provenance before consensus can count.",
        ],
        "next_autonomous_actions": [
            "Regenerate schema/provenance-deficient rows with current packets and a fresh blind judge.",
            "Run an independently framed challenger over current-schema rows without exposing primary calls.",
            "Send disagreements and exception-sensitive rows to an autonomous evidence-fetching arbiter.",
            "Persist model family, prompt hash, packet hash, imagery dates, and each independent decision.",
            "Escalate to a human only after autonomous arbitration still reports contradictory evidence.",
        ],
    }
    if include_items:
        report["items"] = sorted(analyses, key=lambda row: (str(row["area"]), row["key"]))
    return report


def summary_text(report: dict) -> str:
    corpus = report["corpus"]
    routes = report["routing"]
    strict = report["candidate_policies"]["strict"]
    lines = [
        "=== Trekdex parking trust shadow replay ===",
        f"corpus: {corpus['unique_clusters']} clusters from {corpus['source_rows']} source rows "
        f"({corpus['folded_duplicates']} folds)",
        f"verdicts: {corpus['final_verdicts']}",
        f"schema: {corpus['schema_current']} current; "
        f"{corpus['schema_not_current']} legacy/not-current",
        f"human provenance: {report['provenance']['explicit_label_authority']} explicit labels; "
        f"{report['provenance']['human_influenced_union']} influenced union",
        f"routes: {routes['counts']}",
        f"direct user work created: {routes['direct_user_work_created']}",
        f"strict candidates: KEEP {strict['KEEP']['eligible']} / DROP {strict['DROP']['eligible']}",
        f"strict exact identities ready: KEEP={strict['KEEP']['ready_identity_count']} "
        f"(aggregate reference n={strict['KEEP']['promotion_reviewed']}) / "
        f"DROP={strict['DROP']['ready_identity_count']} "
        f"(aggregate reference n={strict['DROP']['promotion_reviewed']})",
        f"measured KEEP: n={report['calibration']['binary']['KEEP']['reviewed']} "
        f"U95={report['calibration']['binary']['KEEP']['wilson_error_upper_95']:.1%}",
        f"measured DROP: n={report['calibration']['binary']['DROP']['reviewed']} "
        f"U95={report['calibration']['binary']['DROP']['wilson_error_upper_95']:.1%}",
        "SHADOW ONLY — no store, sidecar, geom, pool, workflow, or live data changed.",
    ]
    return "\n".join(lines)
