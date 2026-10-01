#!/usr/bin/env python3
"""Pure provenance and consensus rules for autonomous parking resolution.

No function in this module reads or writes files.  Resolver orchestration binds
model identities and exact packet/prompt bytes; persisted validation replays the
same consensus from the immutable envelopes stored in `trust_resolution`.
"""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import ipaddress
import json
import re
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

LEGACY_VERSION = 1
VERSION = 2
LEGACY_POLICY_ID = "parking-trust-resolver-v1"
POLICY_ID = "parking-trust-resolver-v2"
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
REQUIRED_DECISION_FIELDS = frozenset(DECISION_FIELDS) - {"coverage_gap"}
NORMATIVE_DOCUMENT_NAMES = ("judge_protocol.md", "judge_lessons.md")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:/-]*$")
_AREA_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_DNS_LABEL_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_EXTERNAL_LIKE_START_RE = re.compile(r"\[\s*external\b", re.IGNORECASE)
_EXTERNAL_TOKEN_RE = re.compile(
    r"\[external:([a-z0-9][a-z0-9._:/-]*)\]", re.IGNORECASE,
)
_AGENT_URL_LIKE_RE = re.compile(r"(?:https?://|www\.)", re.IGNORECASE)
_DECISION_AXES = ("exists", "public", "serves")


def canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


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


def area_slug(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    slug = value.strip().casefold()
    return slug if _AREA_RE.fullmatch(slug) is not None else None


def _utc_timestamp(value: object) -> _dt.datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = _dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() != _dt.timedelta(0):
        return None
    return parsed


def _canonical_hostname(value: str) -> bool:
    if not value or len(value) > 253 or value.endswith("."):
        return False
    try:
        address = ipaddress.ip_address(value)
    except ValueError:
        return all(_DNS_LABEL_RE.fullmatch(label) is not None
                   for label in value.split("."))
    return str(address) == value


def _source_locator(value: object) -> bool:
    if (not isinstance(value, str) or not value
            or any(ord(character) < 0x20 or ord(character) == 0x7f
                   for character in value)):
        return False
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port  # forces malformed/out-of-range port validation
    except ValueError:
        return False
    if (parsed.scheme != "https" or not parsed.netloc
            or hostname is None or not _canonical_hostname(hostname)
            or parsed.username is not None or parsed.password is not None):
        return False
    host = f"[{hostname}]" if ":" in hostname else hostname
    canonical_netloc = host if port is None else f"{host}:{port}"
    return parsed.netloc == canonical_netloc


def decision_content(decision: dict) -> dict:
    value = copy.deepcopy(decision)
    value.pop("judge_provenance", None)
    value.pop("override", None)
    value.pop("human_confirmation", None)
    value.pop("trust_resolution", None)
    value.pop("publication_attestation", None)
    return value


def decision_projection(decision: dict) -> dict:
    """Return stable decision fields, excluding persistence and outer wrappers."""
    return {
        key: copy.deepcopy(decision[key])
        for key in DECISION_FIELDS
        if key in decision
    }


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
        raw_path = tiles.get(zoom)
        if not isinstance(raw_path, str):
            raise ValueError(f"fid {packet.get('fid')}: missing {zoom} tile {raw_path!r}")
        path = Path(raw_path)
        if (path.is_symlink() or not path.is_file() or path.suffix.casefold() != ".png"):
            raise ValueError(f"fid {packet.get('fid')}: invalid {zoom} tile {str(path)!r}")
        data = path.read_bytes()
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError(f"fid {packet.get('fid')}: {zoom} tile is not PNG data")
        hashes[zoom] = sha256_bytes(data)
    payload = {key: value for key, value in packet.items() if key != "tiles"}
    return payload, hashes


def packet_sha256(packet: dict) -> str:
    payload, tiles = packet_components(packet)
    return sha256_json({"packet": payload, "tile_sha256": tiles})


def area_lock_path(tmp: str | Path, area: str) -> Path:
    canonical_area = area_slug(area)
    if canonical_area is None or canonical_area != area:
        raise ValueError("area lock requires a canonical slug")
    root = Path(tmp).resolve()
    lock_dir = root / ".trust-resolver-locks"
    if lock_dir.exists() and lock_dir.is_symlink():
        raise ValueError("area lock directory may not be a symlink")
    path = lock_dir / f"{area}.lock"
    if path.exists() and path.is_symlink():
        raise ValueError("area lock path may not be a symlink")
    return path


def authority_journal_path(tmp: str | Path, area: str) -> Path:
    """Return the one canonical live human-authority journal for an area."""
    canonical_area = area_slug(area)
    if canonical_area is None or canonical_area != area:
        raise ValueError("authority journal requires a canonical separator-free slug")
    root = Path(tmp).resolve()
    transaction_dir = root / ".human-authority-transactions"
    if transaction_dir.exists() and transaction_dir.is_symlink():
        raise ValueError("authority transaction directory may not be a symlink")
    path = transaction_dir / f"{area}.journal.json"
    if path.exists() and path.is_symlink():
        raise ValueError("authority journal path may not be a symlink")
    return path


def dossier_resource_path(data_dir: str | Path) -> Path:
    """Return the canonical cooperative resource for one dossier inventory."""
    return Path(data_dir).expanduser().resolve() / ".trekdex-dossier-inventory"


def resource_lock_path(tmp: str | Path, resource: str | Path) -> Path:
    del tmp  # resource locks must not vary with the caller's per-area work directory
    resolved = Path(resource).expanduser().resolve()
    identity = sha256_bytes(str(resolved).encode("utf-8"))[:20]
    lock_dir = resolved.parent / ".trekdex-locks"
    if lock_dir.exists() and lock_dir.is_symlink():
        raise ValueError("resource lock directory may not be a symlink")
    path = lock_dir / f"{resolved.name}-{identity}.lock"
    if path.exists() and path.is_symlink():
        raise ValueError("resource lock path may not be a symlink")
    return path


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
        "external_evidence_catalog_sha256": assignment[
            "external_evidence_catalog_sha256"
        ],
        "normative_document_sha256": copy.deepcopy(
            assignment["normative_document_sha256"]
        ),
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
    if any(key in decision for key in ("override", "human_confirmation", "trust_resolution")):
        return [f"{label} decision contains a persisted wrapper"]
    return [
        f"{label} decision: {error}"
        for error in validate_plain(copy.deepcopy(decision))
    ]


def _string_values(value: object, path: tuple[object, ...] = ()):
    if isinstance(value, str):
        yield path, value
    elif isinstance(value, dict):
        for key in sorted(value, key=lambda item: str(item)):
            yield from _string_values(value[key], path + (key,))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _string_values(item, path + (index,))


def _path_text(path: tuple[object, ...]) -> str:
    return ".".join(str(part) for part in path) or "<root>"


def _external_tokens(text: str) -> tuple[list[str], list[str]]:
    """Extract normalized bracketed references and reject external-like syntax."""
    tokens = []
    errors = []
    for start in _EXTERNAL_LIKE_START_RE.finditer(text):
        end = text.find("]", start.start())
        if end < 0:
            errors.append("unclosed external reference")
            continue
        raw = text[start.start():end + 1]
        match = _EXTERNAL_TOKEN_RE.fullmatch(raw)
        canonical_id = identity_key(match.group(1)) if match is not None else None
        if canonical_id is None:
            errors.append(f"malformed external reference {raw!r}")
            continue
        tokens.append(canonical_id)
    return tokens, errors


def _v2_agent_text_errors(envelope: dict, role: str) -> list[str]:
    """Close v2 agent citations over exact axis/source pairs and safe prose."""
    errors = []
    decision = envelope.get("decision")
    actual_pairs: set[tuple[str, str]] = set()
    if isinstance(decision, dict):
        axis_paths = {(axis, "evidence"): axis for axis in _DECISION_AXES}
        for path, text in _string_values(decision):
            path_label = _path_text(path)
            if _AGENT_URL_LIKE_RE.search(text):
                errors.append(
                    f"{role} decision string {path_label} contains an agent-authored "
                    "URL-like locator"
                )
            axis = axis_paths.get(path)
            tokens, token_errors = _external_tokens(text)
            if axis is None:
                if tokens or token_errors:
                    errors.append(
                        f"{role} external reference is outside axis evidence at {path_label}"
                    )
                continue
            errors.extend(
                f"{role} {axis} evidence contains {error}"
                for error in token_errors
            )
            for evidence_id in tokens:
                pair = (axis, evidence_id)
                if pair in actual_pairs:
                    errors.append(
                        f"{role} {axis} evidence repeats external citation "
                        f"{evidence_id!r} after normalization"
                    )
                actual_pairs.add(pair)

    declared_pairs: set[tuple[str, str]] = set()
    external = envelope.get("external_evidence")
    if isinstance(external, list):
        for index, item in enumerate(external):
            if not isinstance(item, dict):
                continue
            evidence_id = identity_key(item.get("id"))
            supports = item.get("supports")
            if evidence_id is None or not isinstance(supports, list):
                continue
            for support_index, support in enumerate(supports):
                if not isinstance(support, dict):
                    continue
                axis = support.get("axis")
                if axis in _DECISION_AXES:
                    declared_pairs.add((axis, evidence_id))
                claim = support.get("claim")
                if isinstance(claim, str):
                    if _AGENT_URL_LIKE_RE.search(claim):
                        errors.append(
                            f"{role} external_evidence[{index}].supports[{support_index}].claim "
                            "contains an agent-authored URL-like locator"
                        )
                    claim_tokens, claim_token_errors = _external_tokens(claim)
                    if claim_tokens or claim_token_errors:
                        errors.append(
                            f"{role} external reference is outside axis evidence at "
                            f"external_evidence.{index}.supports.{support_index}.claim"
                        )

    if role == "challenger" and actual_pairs:
        errors.append("challenger external citations must be absent")

    declared_axes_by_id: dict[str, set[str]] = {}
    for axis, evidence_id in declared_pairs:
        declared_axes_by_id.setdefault(evidence_id, set()).add(axis)
    for axis, evidence_id in sorted(actual_pairs - declared_pairs):
        declared_axes = declared_axes_by_id.get(evidence_id)
        if declared_axes:
            errors.append(
                f"{role} external source {evidence_id!r} is cited in {axis} evidence "
                f"but declared for axes {sorted(declared_axes)}"
            )
        else:
            errors.append(
                f"{role} undeclared external source {evidence_id!r} is cited in "
                f"{axis} evidence"
            )
    for axis, evidence_id in sorted(declared_pairs - actual_pairs):
        errors.append(
            f"{role} external source {evidence_id!r} is not cited in {axis} evidence"
        )
    return errors


def validate_envelope(envelope: object, role: str, packet: dict,
                      validate_plain: Callable[[dict], list[str]],
                      expected_assignment: dict | None = None,
                      expected_packet_sha256: str | None = None,
                      expected_version: int | None = None) -> list[str]:
    label = role
    if not isinstance(envelope, dict):
        return [f"{label} envelope is not an object"]
    errors = []
    envelope_version = envelope.get("version")
    required = {
        "version", "role", "assignment_id", "model", "packet_sha256",
        "prompt_sha256", "input_evidence_sha256", "evidence_sha256",
        "decision_sha256", "envelope_sha256", "external_evidence", "decision",
    }
    if envelope_version == VERSION:
        required.update({
            "normative_document_sha256", "external_evidence_catalog_sha256",
        })
    if set(envelope) != required:
        errors.append(f"{label} envelope keys {sorted(envelope)} != {sorted(required)}")
    if type(envelope_version) is not int or envelope_version not in (LEGACY_VERSION, VERSION):
        errors.append(f"{label} envelope version is {envelope_version!r}")
    if expected_version is not None and envelope_version != expected_version:
        errors.append(
            f"{label} envelope version {envelope_version!r} does not match "
            f"wrapper version {expected_version!r}"
        )
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
            if (not isinstance(model.get(field), str)
                    or identity_key(model[field]) != model[field]):
                errors.append(f"{label} model.{field} is noncanonical")
    normative_hashes = envelope.get("normative_document_sha256")
    if envelope_version == VERSION:
        catalog_hash = envelope.get("external_evidence_catalog_sha256")
        if not isinstance(catalog_hash, str) or _SHA_RE.fullmatch(catalog_hash) is None:
            errors.append(f"{label} external evidence catalog hash is invalid")
        if (not isinstance(normative_hashes, dict)
                or set(normative_hashes) != set(NORMATIVE_DOCUMENT_NAMES)):
            errors.append(f"{label} normative document hashes are malformed")
        else:
            for name in NORMATIVE_DOCUMENT_NAMES:
                value = normative_hashes.get(name)
                if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
                    errors.append(f"{label} normative hash for {name} is invalid")
    input_hashes = envelope.get("input_evidence_sha256")
    if (not isinstance(input_hashes, list)
            or any(not isinstance(value, str) or _SHA_RE.fullmatch(value) is None
                   for value in input_hashes)
            or input_hashes != sorted(set(input_hashes))):
        errors.append(f"{label} input_evidence_sha256 is not a sorted unique SHA-256 list")
    envelope_for_hash = dict(envelope)
    claimed_envelope_hash = envelope_for_hash.pop("envelope_sha256", None)
    if claimed_envelope_hash != sha256_json(envelope_for_hash):
        errors.append(f"{label} envelope hash mismatch")
    external = envelope.get("external_evidence")
    if not isinstance(external, list):
        errors.append(f"{label} external_evidence is not a list")
    else:
        if envelope_version == VERSION and role == "challenger" and external:
            errors.append("challenger external_evidence must be empty")
        seen_ids = set()
        seen_hashes = set()
        for index, item in enumerate(external):
            expected_keys = (
                {"id", "sha256"}
                if envelope_version == LEGACY_VERSION
                else {
                    "id", "sha256", "source_locator", "retrieved_at",
                    "source_updated_at", "frozen_path", "manifest_sha256",
                    "manifest_frozen_path", "metadata_sha256",
                    "metadata_frozen_path", "supports",
                }
            )
            if not isinstance(item, dict) or set(item) != expected_keys:
                errors.append(f"{label} external_evidence[{index}] is malformed")
                continue
            if (not isinstance(item.get("id"), str)
                    or identity_key(item["id"]) != item["id"]):
                errors.append(f"{label} external_evidence[{index}].id is noncanonical")
            if _SHA_RE.fullmatch(str(item["sha256"])) is None:
                errors.append(f"{label} external_evidence[{index}].sha256 is invalid")
            evidence_id = item.get("id")
            evidence_hash = item.get("sha256")
            if isinstance(evidence_id, str) and isinstance(evidence_hash, str):
                if evidence_id in seen_ids or evidence_hash in seen_hashes:
                    errors.append(f"{label} external_evidence contains a duplicate id or hash")
                seen_ids.add(evidence_id)
                seen_hashes.add(evidence_hash)
            if envelope_version == VERSION:
                for hash_field in ("manifest_sha256", "metadata_sha256"):
                    value = item.get(hash_field)
                    if not isinstance(value, str) or _SHA_RE.fullmatch(value) is None:
                        errors.append(
                            f"{label} external_evidence[{index}].{hash_field} is invalid"
                        )
                if (item.get("frozen_path") != f"evidence/{evidence_hash}.bin"
                        or item.get("manifest_frozen_path")
                        != f"evidence/{item.get('manifest_sha256')}.manifest.json"
                        or item.get("metadata_frozen_path")
                        != f"evidence/{item.get('metadata_sha256')}.metadata.json"):
                    errors.append(
                        f"{label} external_evidence[{index}] frozen paths are noncanonical"
                    )
                if (isinstance(evidence_hash, str)
                        and evidence_hash in (input_hashes if isinstance(input_hashes, list) else [])):
                    errors.append(
                        f"{label} external_evidence[{index}] reuses bound packet/input bytes"
                    )
                if not _source_locator(item.get("source_locator")):
                    errors.append(
                        f"{label} external_evidence[{index}].source_locator is not a canonical HTTPS URL"
                    )
                retrieved_at = _utc_timestamp(item.get("retrieved_at"))
                if retrieved_at is None:
                    errors.append(
                        f"{label} external_evidence[{index}].retrieved_at is not a UTC timestamp"
                    )
                source_updated_raw = item.get("source_updated_at")
                source_updated_at = (
                    None if source_updated_raw is None
                    else _utc_timestamp(source_updated_raw)
                )
                if source_updated_raw is not None and source_updated_at is None:
                    errors.append(
                        f"{label} external_evidence[{index}].source_updated_at is not UTC or null"
                    )
                if (retrieved_at is not None and source_updated_at is not None
                        and source_updated_at > retrieved_at):
                    errors.append(
                        f"{label} external_evidence[{index}] source update is after retrieval"
                    )
                supports = item.get("supports")
                if not isinstance(supports, list) or not supports:
                    errors.append(f"{label} external_evidence[{index}].supports is empty")
                    continue
                support_axes = set()
                for support_index, support in enumerate(supports):
                    if not isinstance(support, dict) or set(support) != {"axis", "call", "claim"}:
                        errors.append(
                            f"{label} external_evidence[{index}].supports[{support_index}] is malformed"
                        )
                        continue
                    axis = support.get("axis")
                    call = support.get("call")
                    claim = support.get("claim")
                    if axis not in _DECISION_AXES:
                        errors.append(f"{label} external support axis is {axis!r}")
                    if call not in ("yes", "no", "unclear", "n/a"):
                        errors.append(f"{label} external support call is {call!r}")
                    if not isinstance(claim, str) or not claim.strip():
                        errors.append(f"{label} external support claim is empty")
                    if axis in _DECISION_AXES:
                        if axis in support_axes:
                            errors.append(
                                f"{label} external source {evidence_id!r} repeats support "
                                f"for axis {axis!r}"
                            )
                        support_axes.add(axis)
                    decision_value = envelope.get("decision")
                    axis_value = (
                        decision_value.get(axis)
                        if isinstance(decision_value, dict) and axis in _DECISION_AXES
                        else None
                    )
                    if (isinstance(axis_value, dict)
                            and axis_value.get("call") != call):
                        errors.append(
                            f"{label} external support {axis!r} call does not match decision"
                        )
    decision = envelope.get("decision")
    if envelope_version == VERSION and role in ("challenger", "arbiter"):
        errors.extend(_v2_agent_text_errors(envelope, role))
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
                      "external_evidence_catalog_sha256",
                      "normative_document_sha256", "input_evidence_sha256"):
            if envelope.get(field) != expected_assignment.get(field):
                errors.append(f"{label} {field} does not match assignment")
    return errors


def family(envelope: object) -> str | None:
    if not isinstance(envelope, dict):
        return None
    model = envelope.get("model")
    if not isinstance(model, dict):
        return None
    return identity_key(model.get("family"))


def model_id(envelope: object) -> str | None:
    if not isinstance(envelope, dict):
        return None
    model = envelope.get("model")
    if not isinstance(model, dict):
        return None
    return identity_key(model.get("id"))


def _binary_confident(envelope: dict | None) -> bool:
    decision = (envelope or {}).get("decision") or {}
    return (decision.get("verdict") in ("KEEP", "DROP")
            and decision.get("confidence") in ("certain", "strong"))


def _novel_cited_evidence_v1(arbiter: dict, prior: tuple[dict, ...]) -> bool:
    """Replay the frozen v1 hash-plus-citation policy for persisted rows."""
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


def _decision_axis_calls(envelope: dict | None) -> tuple[object, object, object, object]:
    decision = (envelope or {}).get("decision") or {}
    return (
        decision.get("verdict"),
        (decision.get("exists") or {}).get("call"),
        (decision.get("public") or {}).get("call"),
        (decision.get("serves") or {}).get("call"),
    )


def resolve(route: str, primary: dict, challenger: dict | None,
            arbiter: dict | None, policy_version: int = VERSION) -> dict:
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

    if route == "AUTONOMOUS_ARBITER" and policy_version == VERSION and not _binary_confident(primary):
        return {"state": "human_exception", "result": None, "selected_role": None,
                "reason": "v2 nonbinary primary requires independent or human authority"}

    parents = (primary,) if route == "AUTONOMOUS_ARBITER" else (primary, challenger)
    if route == "AUTONOMOUS_BLIND_CHALLENGE":
        if challenger is None:
            return {"state": "pending", "result": None, "selected_role": None,
                    "reason": "challenger decision missing"}
        agreement = (
            primary["decision"].get("verdict") == challenger["decision"].get("verdict")
            if policy_version == LEGACY_VERSION
            else _decision_axis_calls(primary) == _decision_axis_calls(challenger)
        )
        clean_agreement = (
            _binary_confident(primary)
            and _binary_confident(challenger)
            and agreement
            and family(primary) != family(challenger)
            and model_id(primary) != model_id(challenger)
        )
        if clean_agreement:
            return {"state": "resolved", "result": "DISTINCT_FAMILY_AGREEMENT",
                    "selected_role": "challenger", "reason": "blind distinct-family agreement"}
        if (policy_version == VERSION
                and family(primary) == family(challenger)
                and _decision_axis_calls(primary) != _decision_axis_calls(challenger)):
            return {"state": "human_exception", "result": None, "selected_role": None,
                    "reason": "v2 same-family parents disagree on verdict or axis calls"}
    if arbiter is None:
        return {"state": "pending", "result": None, "selected_role": None,
                "reason": "arbiter decision required"}
    if not _binary_confident(arbiter):
        return {"state": "human_exception", "result": None, "selected_role": None,
                "reason": "arbiter did not produce a confident binary decision"}

    available_parents = tuple(envelope for envelope in parents if envelope is not None)
    if policy_version == VERSION:
        participants = available_parents + (arbiter,)
        participant_ids = [model_id(envelope) for envelope in participants]
        participant_families = [family(envelope) for envelope in participants]
        if (len(set(participant_ids)) != len(participant_ids)
                or len(set(participant_families)) != len(participant_families)):
            return {"state": "human_exception", "result": None,
                    "selected_role": None,
                    "reason": "v2 participating votes contain a duplicate model id or family"}
    if policy_version == LEGACY_VERSION:
        arbiter_verdict = arbiter["decision"]["verdict"]
        matching = [
            envelope for envelope in available_parents
            if _binary_confident(envelope)
            and envelope["decision"]["verdict"] == arbiter_verdict
        ]
        distinct_match = any(
            family(envelope) != family(arbiter)
            and model_id(envelope) != model_id(arbiter)
            for envelope in matching
        )
    else:
        arbiter_calls = _decision_axis_calls(arbiter)
        matching = [
            envelope for envelope in available_parents
            if _binary_confident(envelope)
            and _decision_axis_calls(envelope) == arbiter_calls
        ]
        distinct_match = bool(matching) and all(
            family(envelope) != family(arbiter)
            and model_id(envelope) != model_id(arbiter)
            for envelope in available_parents
        )
    if distinct_match:
        return {"state": "resolved", "result": "ARBITRATED_AGREEMENT",
                "selected_role": "arbiter",
                "reason": "arbiter exactly agrees and is distinct from every parent"
                if policy_version == VERSION
                else "arbiter agrees with a distinct family"}
    if policy_version == VERSION:
        return {"state": "human_exception", "result": None, "selected_role": None,
                "reason": "v2 arbitration requires exact axis agreement and independence from every parent"}
    if _novel_cited_evidence_v1(arbiter, available_parents):
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


_PERSISTENCE_FIELDS = {
    "src", "judged", "lat", "lon", "rings", "name", "_key", "reason",
    "publication_attestation",
}


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


def validate_persisted_resolution(machine_row: object, packet: object,
                                  validate_plain: Callable[[dict], list[str]],
                                  expected_packet_sha256: str | None = None) -> list[str]:
    if not isinstance(machine_row, dict):
        return ["machine row must be an object"]
    if not isinstance(packet, dict):
        return ["packet must be an object"]
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
    wrapper_version = wrapper.get("version")
    valid_version_policy = type(wrapper_version) is int and (
        (wrapper_version == LEGACY_VERSION and wrapper.get("policy_id") == LEGACY_POLICY_ID)
        or (wrapper_version == VERSION and wrapper.get("policy_id") == POLICY_ID)
    )
    if not valid_version_policy:
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
    selected_role_valid = selected_role in ROLES
    if not selected_role_valid:
        errors.append("trust_resolution selected_role is invalid")

    envelopes = {role: wrapper.get(role) for role in ROLES}
    for role, envelope in envelopes.items():
        if envelope is None and role != "primary":
            continue
        errors.extend(validate_envelope(
            envelope, role, packet, validate_plain,
            expected_packet_sha256=expected_packet_sha256,
            expected_version=wrapper_version,
        ))
    present_envelopes = [value for value in envelopes.values() if isinstance(value, dict)]
    packet_hashes = [value.get("packet_sha256") for value in present_envelopes]
    if (packet_hashes
            and all(isinstance(value, str) and _SHA_RE.fullmatch(value) is not None
                    for value in packet_hashes)
            and len(set(packet_hashes)) != 1):
        errors.append("resolution envelopes do not share one packet hash")

    selected = envelopes[selected_role] if selected_role_valid else None
    if selected_role_valid and selected is None and selected_role != "primary":
        errors.append(f"selected {selected_role} envelope is missing")
    if isinstance(selected, dict):
        if wrapper.get("selected_decision_sha256") != selected.get("decision_sha256"):
            errors.append("selected decision hash does not match selected envelope")
        selected_decision = selected.get("decision")
        if (isinstance(selected_decision, dict)
                and _top_decision_content(machine_row) != decision_content(selected_decision)):
            errors.append("top-level machine decision differs from selected decision")
        model = selected.get("model")
        provenance_hash_fields = (
            "decision_sha256", "packet_sha256", "prompt_sha256", "evidence_sha256",
        )
        provenance_ready = (
            isinstance(model, dict)
            and set(model) == {"id", "family", "provider", "build"}
            and all(isinstance(model.get(field), str)
                    and identity_key(model[field]) == model[field]
                    for field in ("id", "family", "provider", "build"))
            and all(isinstance(selected.get(field), str)
                    and _SHA_RE.fullmatch(selected[field]) is not None
                    for field in provenance_hash_fields)
        )
        if (provenance_ready
                and machine_row.get("judge_provenance") != expected_judge_provenance(selected)):
            errors.append("top-level judge_provenance differs from selected envelope")

    if not errors and isinstance(envelopes.get("primary"), dict):
        recomputed = resolve(wrapper.get("route"), envelopes["primary"],
                             envelopes.get("challenger"), envelopes.get("arbiter"),
                             policy_version=wrapper_version)
        for field in ("state", "result", "selected_role"):
            if recomputed.get(field) != ({"state": "resolved", "result": wrapper.get("result"),
                                         "selected_role": selected_role}).get(field):
                errors.append("persisted consensus does not replay to the stored result")
                break
    correlation = wrapper.get("correlation")
    if not isinstance(correlation, dict):
        errors.append("trust_resolution correlation is not an object")
    else:
        valid_models = []
        for envelope in present_envelopes:
            model = envelope.get("model")
            if (not isinstance(model, dict)
                    or set(model) != {"id", "family", "provider", "build"}
                    or any(not isinstance(model.get(field), str)
                           or identity_key(model[field]) != model[field]
                           for field in ("id", "family", "provider", "build"))):
                break
            valid_models.append(model)
        if len(valid_models) == len(present_envelopes):
            expected_families = sorted({model["family"] for model in valid_models})
            expected_ids = sorted({model["id"] for model in valid_models})
            expected = {
                "model_ids": expected_ids,
                "model_families": expected_families,
                "same_model_family": len(expected_families) < len(present_envelopes),
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
