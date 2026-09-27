#!/usr/bin/env python3
"""Pure provenance and consensus rules for autonomous parking resolution.

No function in this module reads or writes files.  Resolver orchestration binds
model identities and exact packet/prompt bytes; persisted validation replays the
same consensus from the immutable envelopes stored in `trust_resolution`.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Callable

VERSION = 1
POLICY_ID = "parking-trust-resolver-v1"
ROLES = ("primary", "challenger", "arbiter")
ROUTES = (
    "PRESERVE_HUMAN_AUTHORITY",
    "PRESERVE_MACHINE_RESOLUTION",
    "DIRECT_AUTO_KEEP",
    "DIRECT_AUTO_DROP",
    "AUTONOMOUS_REFRESH",
    "AUTONOMOUS_BLIND_CHALLENGE",
    "AUTONOMOUS_ARBITER",
)
RESULTS = (
    "PRESERVED",
    "DIRECT_PROMOTED_CLASS",
    "DISTINCT_FAMILY_AGREEMENT",
    "ARBITRATED_AGREEMENT",
    "CORRELATED_EVIDENCE_ARBITRATION",
)
DECISION_FIELDS = (
    "fid", "area", "osm", "verdict", "prior", "exists", "public", "serves",
    "frames_used", "tags_cited", "confidence", "resolve_hint", "coverage_gap",
)
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:/-]*$")


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: object) -> str:
    return sha256_bytes(canonical_json(value))


def identity_key(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    key = value.strip().casefold()
    if key == "unknown" or _ID_RE.fullmatch(key) is None:
        return None
    return key


def decision_content(decision: dict) -> dict:
    value = copy.deepcopy(decision)
    value.pop("judge_provenance", None)
    value.pop("override", None)
    value.pop("trust_resolution", None)
    return value


def decision_sha256(decision: dict) -> str:
    return sha256_json(decision_content(decision))


def evidence_sha256(decision: dict) -> str:
    return sha256_json({
        "exists": decision.get("exists"),
        "public": decision.get("public"),
        "serves": decision.get("serves"),
        "frames_used": decision.get("frames_used"),
        "tags_cited": decision.get("tags_cited"),
        "coverage_gap": decision.get("coverage_gap"),
        "resolve_hint": decision.get("resolve_hint"),
    })


def packet_components(packet: dict) -> tuple[dict, dict[str, str]]:
    """Return canonical packet data and exact image hashes, excluding paths."""
    tiles = packet.get("tiles")
    if not isinstance(tiles, dict):
        raise ValueError(f"fid {packet.get('fid')}: tiles is not an object")
    hashes = {}
    for zoom in ("z1", "z2", "z3"):
        path = tiles.get(zoom)
        if not isinstance(path, str) or not Path(path).is_file():
            raise ValueError(f"fid {packet.get('fid')}: missing {zoom} tile {path!r}")
        hashes[zoom] = sha256_bytes(Path(path).read_bytes())
    payload = {key: value for key, value in packet.items() if key != "tiles"}
    return payload, hashes


def packet_sha256(packet: dict) -> str:
    payload, tiles = packet_components(packet)
    return sha256_json({"packet": payload, "tile_sha256": tiles})


def area_lock_path(tmp: str | Path, area: str) -> Path:
    canonical_area = identity_key(area)
    if canonical_area is None or canonical_area != area:
        raise ValueError("area lock requires a canonical slug")
    return Path(tmp) / ".trust-resolver-locks" / f"{area}.lock"


def resource_lock_path(tmp: str | Path, resource: str | Path) -> Path:
    del tmp  # resource locks must not vary with the caller's per-area work directory
    resolved = Path(resource).expanduser().resolve()
    identity = sha256_bytes(str(resolved).encode("utf-8"))[:20]
    return resolved.parent / ".trekdex-locks" / f"{resolved.name}-{identity}.lock"


def model_config(model_id: object, family: object, provider: object = "kiro",
                 build: object = "unspecified") -> dict:
    values = {
        "id": identity_key(model_id),
        "family": identity_key(family),
        "provider": identity_key(provider),
        "build": identity_key(build),
    }
    bad = [key for key, value in values.items() if value is None]
    if bad:
        raise ValueError("noncanonical model config field(s): " + ", ".join(bad))
    return values


def make_envelope(role: str, assignment: dict, decision: dict,
                  external_evidence: list[dict] | None = None) -> dict:
    if role not in ROLES:
        raise ValueError(f"unknown role {role!r}")
    envelope = {
        "version": VERSION,
        "role": role,
        "assignment_id": assignment["assignment_id"],
        "model": copy.deepcopy(assignment["model"]),
        "packet_sha256": assignment["packet_sha256"],
        "prompt_sha256": assignment["prompt_sha256"],
        "input_evidence_sha256": sorted(assignment.get("input_evidence_sha256") or []),
        "evidence_sha256": evidence_sha256(decision),
        "decision_sha256": decision_sha256(decision),
        "external_evidence": copy.deepcopy(external_evidence or []),
        "decision": copy.deepcopy(decision),
    }
    envelope["envelope_sha256"] = sha256_json(envelope)
    return envelope


def _plain_errors(validate_plain: Callable[[dict], list[str]], decision: object,
                  label: str) -> list[str]:
    if not isinstance(decision, dict):
        return [f"{label} decision is not an object"]
    if "override" in decision or "trust_resolution" in decision:
        return [f"{label} decision contains a persisted wrapper"]
    return [f"{label} decision: {error}" for error in validate_plain(decision)]


def validate_envelope(envelope: object, role: str, packet: dict,
                      validate_plain: Callable[[dict], list[str]],
                      expected_assignment: dict | None = None,
                      expected_packet_sha256: str | None = None) -> list[str]:
    label = role
    if not isinstance(envelope, dict):
        return [f"{label} envelope is not an object"]
    errors = []
    required = {
        "version", "role", "assignment_id", "model", "packet_sha256",
        "prompt_sha256", "input_evidence_sha256", "evidence_sha256",
        "decision_sha256", "envelope_sha256", "external_evidence", "decision",
    }
    if set(envelope) != required:
        errors.append(f"{label} envelope keys {sorted(envelope)} != {sorted(required)}")
    if envelope.get("version") != VERSION:
        errors.append(f"{label} envelope version is {envelope.get('version')!r}")
    if envelope.get("role") != role:
        errors.append(f"{label} envelope role is {envelope.get('role')!r}")
    for field in ("assignment_id", "packet_sha256", "prompt_sha256",
                  "evidence_sha256", "decision_sha256", "envelope_sha256"):
        if not isinstance(envelope.get(field), str) or _SHA_RE.fullmatch(envelope[field]) is None:
            errors.append(f"{label} {field} is not a lowercase SHA-256")
    model = envelope.get("model")
    if not isinstance(model, dict) or set(model) != {"id", "family", "provider", "build"}:
        errors.append(f"{label} model identity is malformed")
    else:
        for field in ("id", "family", "provider", "build"):
            if identity_key(model.get(field)) != model.get(field):
                errors.append(f"{label} model.{field} is noncanonical")
    input_hashes = envelope.get("input_evidence_sha256")
    if (not isinstance(input_hashes, list) or input_hashes != sorted(set(input_hashes))
            or any(not isinstance(value, str) or _SHA_RE.fullmatch(value) is None
                   for value in input_hashes)):
        errors.append(f"{label} input_evidence_sha256 is not a sorted unique SHA-256 list")
    envelope_for_hash = dict(envelope)
    claimed_envelope_hash = envelope_for_hash.pop("envelope_sha256", None)
    if claimed_envelope_hash != sha256_json(envelope_for_hash):
        errors.append(f"{label} envelope hash mismatch")
    external = envelope.get("external_evidence")
    if not isinstance(external, list):
        errors.append(f"{label} external_evidence is not a list")
    else:
        seen = set()
        for index, item in enumerate(external):
            if not isinstance(item, dict) or set(item) != {"id", "sha256"}:
                errors.append(f"{label} external_evidence[{index}] is malformed")
                continue
            if identity_key(item.get("id")) != item.get("id"):
                errors.append(f"{label} external_evidence[{index}].id is noncanonical")
            if _SHA_RE.fullmatch(str(item["sha256"])) is None:
                errors.append(f"{label} external_evidence[{index}].sha256 is invalid")
            identity = (item.get("id"), item.get("sha256"))
            if identity in seen:
                errors.append(f"{label} external_evidence contains a duplicate")
            seen.add(identity)
    decision = envelope.get("decision")
    errors.extend(_plain_errors(validate_plain, decision, label))
    if isinstance(decision, dict):
        for field in ("fid", "area", "osm", "prior"):
            if decision.get(field) != packet.get(field):
                errors.append(f"{label} decision {field} does not match packet")
        if envelope.get("decision_sha256") != decision_sha256(decision):
            errors.append(f"{label} decision hash mismatch")
        if envelope.get("evidence_sha256") != evidence_sha256(decision):
            errors.append(f"{label} evidence hash mismatch")
    if expected_packet_sha256 is not None and envelope.get("packet_sha256") != expected_packet_sha256:
        errors.append(f"{label} packet hash mismatch")
    if expected_assignment is not None:
        for field in ("assignment_id", "packet_sha256", "prompt_sha256", "model",
                      "input_evidence_sha256"):
            if envelope.get(field) != expected_assignment.get(field):
                errors.append(f"{label} {field} does not match assignment")
    return errors


def family(envelope: dict | None) -> str | None:
    return identity_key(((envelope or {}).get("model") or {}).get("family"))


def model_id(envelope: dict | None) -> str | None:
    return identity_key(((envelope or {}).get("model") or {}).get("id"))


def _binary_confident(envelope: dict | None) -> bool:
    decision = (envelope or {}).get("decision") or {}
    return (decision.get("verdict") in ("KEEP", "DROP")
            and decision.get("confidence") in ("certain", "strong"))


def _novel_cited_evidence(arbiter: dict, prior: tuple[dict, ...]) -> bool:
    external = arbiter.get("external_evidence") or []
    known_hashes = {
        value for envelope in prior
        for value in ((envelope.get("input_evidence_sha256") or [])
                      + [item.get("sha256") for item in envelope.get("external_evidence") or []])
        if value
    }
    decision = arbiter.get("decision") or {}
    cited_text = " ".join(
        str((decision.get(axis) or {}).get("evidence") or "")
        for axis in ("exists", "public", "serves")
    ).casefold()
    novel = [item for item in external if item.get("sha256") not in known_hashes]
    return bool(novel) and all(
        f"[external:{item.get('id')}]".casefold() in cited_text for item in novel
    )


def resolve(route: str, primary: dict, challenger: dict | None,
            arbiter: dict | None) -> dict:
    """Return a deterministic operational resolution; never a trust promotion."""
    if route in ("PRESERVE_HUMAN_AUTHORITY", "PRESERVE_MACHINE_RESOLUTION"):
        return {"state": "preserved", "result": "PRESERVED", "selected_role": None,
                "reason": "existing authoritative layer is immutable"}
    if route in ("DIRECT_AUTO_KEEP", "DIRECT_AUTO_DROP"):
        return {"state": "resolved", "result": "DIRECT_PROMOTED_CLASS",
                "selected_role": "primary", "reason": "class promotion already passed"}
    if route == "AUTONOMOUS_REFRESH":
        return {"state": "blocked", "result": None, "selected_role": None,
                "reason": "fresh primary decision required before resolution"}

    parents = (primary,) if route == "AUTONOMOUS_ARBITER" else (primary, challenger)
    if route == "AUTONOMOUS_BLIND_CHALLENGE":
        if challenger is None:
            return {"state": "pending", "result": None, "selected_role": None,
                    "reason": "challenger decision missing"}
        pd = primary["decision"]
        cd = challenger["decision"]
        clean_agreement = (
            _binary_confident(primary)
            and _binary_confident(challenger)
            and pd["verdict"] == cd["verdict"]
            and family(primary) != family(challenger)
            and model_id(primary) != model_id(challenger)
        )
        if clean_agreement:
            return {"state": "resolved", "result": "DISTINCT_FAMILY_AGREEMENT",
                    "selected_role": "challenger", "reason": "blind distinct-family agreement"}
    if arbiter is None:
        return {"state": "pending", "result": None, "selected_role": None,
                "reason": "arbiter decision required"}
    if not _binary_confident(arbiter):
        return {"state": "human_exception", "result": None, "selected_role": None,
                "reason": "arbiter did not produce a confident binary decision"}

    available_parents = tuple(envelope for envelope in parents if envelope is not None)
    arbiter_verdict = arbiter["decision"]["verdict"]
    matching = [envelope for envelope in available_parents
                if _binary_confident(envelope)
                and envelope["decision"]["verdict"] == arbiter_verdict]
    distinct_match = any(
        family(envelope) != family(arbiter) and model_id(envelope) != model_id(arbiter)
        for envelope in matching
    )
    if distinct_match:
        return {"state": "resolved", "result": "ARBITRATED_AGREEMENT",
                "selected_role": "arbiter", "reason": "arbiter agrees with a distinct family"}
    if _novel_cited_evidence(arbiter, available_parents):
        return {"state": "resolved", "result": "CORRELATED_EVIDENCE_ARBITRATION",
                "selected_role": "arbiter", "reason": "arbiter added novel cited evidence"}
    return {"state": "human_exception", "result": None, "selected_role": None,
            "reason": "correlated decisions remain unresolved without novel cited evidence"}


def expected_judge_provenance(envelope: dict) -> dict:
    return {
        "model_id": envelope["model"]["id"],
        "model_family": envelope["model"]["family"],
        "decision_sha256": envelope["decision_sha256"],
        "packet_sha256": envelope["packet_sha256"],
        "prompt_sha256": envelope["prompt_sha256"],
        "evidence_sha256": envelope["evidence_sha256"],
    }


_PERSISTENCE_FIELDS = {"src", "judged", "lat", "lon", "rings", "name", "_key", "reason"}


def _top_decision_content(row: dict) -> dict:
    value = decision_content(row)
    return {key: item for key, item in value.items() if key not in _PERSISTENCE_FIELDS}


def make_resolved_row(primary_row: dict, route: str, resolution: dict,
                      envelopes: dict[str, dict | None], run_id: str,
                      transaction_id: str) -> dict:
    role = resolution.get("selected_role")
    if resolution.get("state") != "resolved" or role not in ROLES:
        raise ValueError("resolution is not apply-ready")
    selected = envelopes[role]
    if selected is None:
        raise ValueError(f"selected {role} envelope is missing")
    primary = envelopes.get("primary")
    if not isinstance(primary, dict) or decision_content(primary.get("decision") or {}) != decision_content(primary_row):
        raise ValueError("primary envelope does not preserve the exact pre-resolution decision")
    row = copy.deepcopy(selected["decision"])
    row["judge_provenance"] = expected_judge_provenance(selected)
    families = sorted({family(value) for value in envelopes.values() if value is not None})
    wrapper = {
        "version": VERSION,
        "policy_id": POLICY_ID,
        "run_id": run_id,
        "transaction_id": transaction_id,
        "route": route,
        "result": resolution["result"],
        "selected_role": role,
        "selected_decision_sha256": selected["decision_sha256"],
        "primary": copy.deepcopy(envelopes["primary"]),
        "challenger": copy.deepcopy(envelopes.get("challenger")),
        "arbiter": copy.deepcopy(envelopes.get("arbiter")),
        "correlation": {
            "model_ids": sorted({model_id(value) for value in envelopes.values() if value is not None}),
            "model_families": families,
            "same_model_family": len(families) < len([v for v in envelopes.values() if v is not None]),
            "basis": resolution["result"],
        },
    }
    wrapper["resolution_sha256"] = sha256_json(wrapper)
    row["trust_resolution"] = wrapper
    return row


def validate_persisted_resolution(machine_row: dict, packet: dict,
                                  validate_plain: Callable[[dict], list[str]],
                                  expected_packet_sha256: str | None = None) -> list[str]:
    wrapper = machine_row.get("trust_resolution")
    if not isinstance(wrapper, dict):
        return ["trust_resolution must be an object"]
    errors = []
    required = {
        "version", "policy_id", "run_id", "transaction_id", "route", "result",
        "selected_role", "selected_decision_sha256", "primary", "challenger",
        "arbiter", "correlation", "resolution_sha256",
    }
    if set(wrapper) != required:
        errors.append(f"trust_resolution keys {sorted(wrapper)} != {sorted(required)}")
    if wrapper.get("version") != VERSION or wrapper.get("policy_id") != POLICY_ID:
        errors.append("trust_resolution version/policy mismatch")
    for field in ("run_id", "transaction_id", "selected_decision_sha256", "resolution_sha256"):
        if not isinstance(wrapper.get(field), str) or _SHA_RE.fullmatch(wrapper[field]) is None:
            errors.append(f"trust_resolution {field} is not a SHA-256")
    if wrapper.get("route") not in ROUTES:
        errors.append("trust_resolution route is invalid")
    if wrapper.get("result") not in RESULTS:
        errors.append("trust_resolution result is invalid")
    wrapper_for_hash = dict(wrapper)
    claimed_resolution_hash = wrapper_for_hash.pop("resolution_sha256", None)
    if claimed_resolution_hash != sha256_json(wrapper_for_hash):
        errors.append("trust_resolution wrapper hash mismatch")
    selected_role = wrapper.get("selected_role")
    if selected_role not in ROLES:
        errors.append("trust_resolution selected_role is invalid")

    envelopes = {role: wrapper.get(role) for role in ROLES}
    for role, envelope in envelopes.items():
        if envelope is None and role != "primary":
            continue
        errors.extend(validate_envelope(
            envelope, role, packet, validate_plain,
            expected_packet_sha256=expected_packet_sha256,
        ))
    present_envelopes = [value for value in envelopes.values() if isinstance(value, dict)]
    packet_hashes = {value.get("packet_sha256") for value in present_envelopes}
    if len(packet_hashes) != 1:
        errors.append("resolution envelopes do not share one packet hash")
    selected = envelopes.get(selected_role)
    if isinstance(selected, dict):
        if wrapper.get("selected_decision_sha256") != selected.get("decision_sha256"):
            errors.append("selected decision hash does not match selected envelope")
        if _top_decision_content(machine_row) != decision_content(selected.get("decision") or {}):
            errors.append("top-level machine decision differs from selected decision")
        if machine_row.get("judge_provenance") != expected_judge_provenance(selected):
            errors.append("top-level judge_provenance differs from selected envelope")

    if isinstance(envelopes.get("primary"), dict):
        recomputed = resolve(wrapper.get("route"), envelopes["primary"],
                             envelopes.get("challenger"), envelopes.get("arbiter"))
        for field in ("state", "result", "selected_role"):
            if recomputed.get(field) != ({"state": "resolved", "result": wrapper.get("result"),
                                         "selected_role": selected_role}).get(field):
                errors.append("persisted consensus does not replay to the stored result")
                break
    correlation = wrapper.get("correlation")
    if not isinstance(correlation, dict):
        errors.append("trust_resolution correlation is not an object")
    else:
        present = [value for value in envelopes.values() if isinstance(value, dict)]
        expected_families = sorted({family(value) for value in present})
        expected_ids = sorted({model_id(value) for value in present})
        expected = {
            "model_ids": expected_ids,
            "model_families": expected_families,
            "same_model_family": len(expected_families) < len(present),
            "basis": wrapper.get("result"),
        }
        if correlation != expected:
            errors.append("trust_resolution correlation summary mismatch")
    return errors


def primary_decision(machine_row: dict) -> dict | None:
    wrapper = machine_row.get("trust_resolution")
    envelope = wrapper.get("primary") if isinstance(wrapper, dict) else None
    decision = envelope.get("decision") if isinstance(envelope, dict) else None
    return copy.deepcopy(decision) if isinstance(decision, dict) else None
