#!/usr/bin/env python3
"""Shared validation/discovery rules for parking judge drafts.

The checkpoint generator, review sheet, calibration ledger, and final store
merger must all read exactly the same canonical draft files and agree on what a
legal verdict row is. This module contains no writes.
"""
from __future__ import annotations

import copy
import datetime as _dt
import json
import re
from pathlib import Path

import trust_resolution as tr

REQUIRED = {
    "fid", "area", "osm", "verdict", "prior", "exists", "public", "serves",
    "frames_used", "tags_cited", "confidence", "resolve_hint",
}
VERDICTS = ("KEEP", "DROP", "REVIEW")
CONFIDENCE = ("certain", "strong", "leaning")
CALLS = ("yes", "no", "unclear", "n/a")
FRAME_RE = re.compile(r"^z[123](?:_naip)?$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
LEGACY_CONFIRMATION_VERSION = 1
CONFIRMATION_VERSION = 2
LEGACY_OVERRIDE_VERSION = 2
OVERRIDE_VERSION = 3
LEGACY_AUTHORITY_RECEIPT_VERSION = 1
AUTHORITY_RECEIPT_VERSION = 2
AUTHORITY_RECEIPT_KIND = "parking-human-authority-receipt"
AUTHORITY_RECEIPT_DIR = ".human-authority"
LEGACY_PUBLICATION_ATTESTATION_VERSION = 1
PUBLICATION_ATTESTATION_VERSION = 2
PUBLICATION_ATTESTATION_KIND = "parking-publication-attestation"


def current_authority(row: object) -> tuple[str, str, str] | None:
    """Return (kind, authority hash, packet hash) for current-schema authority."""
    if not isinstance(row, dict):
        return None
    bound = authority_wrapper(row)
    if bound is not None:
        kind, wrapper = bound
        return kind, wrapper.get("authority_receipt_sha256"), wrapper.get("packet_sha256")
    trust = row.get("trust_resolution")
    if (isinstance(trust, dict) and type(trust.get("version")) is int
            and trust.get("version") == tr.VERSION):
        authority_hash = trust.get("resolution_sha256")
        provenance = row.get("judge_provenance")
        packet_hash = provenance.get("packet_sha256") if isinstance(provenance, dict) else None
        return "machine_v2", authority_hash, packet_hash
    return None


def publication_projection(row: dict) -> dict:
    return {
        "area": row.get("area"),
        "fid": row.get("fid"),
        "osm": copy.deepcopy(row.get("osm")),
        "lat": row.get("lat"),
        "lon": row.get("lon"),
        "rings": copy.deepcopy(row.get("rings")),
        "name": row.get("name"),
        "src": row.get("src"),
        "judged": row.get("judged"),
    }


def validate_publication_attestation(row: object) -> list[str]:
    if not isinstance(row, dict):
        return ["publication row is not an object"]
    authority = current_authority(row)
    attestation = row.get("publication_attestation")
    if authority is None:
        return ["unexpected publication_attestation without current authority"] if attestation is not None else []
    if not isinstance(attestation, dict):
        return ["current authority requires publication_attestation"]
    version = attestation.get("version")
    common = {
        "version", "kind", "area", "fid", "verdict", "decision_sha256",
        "evidence_sha256", "packet_sha256", "authority_kind", "authority_sha256",
        "resolver_run_id", "output_seal_sha256", "terminal_receipt_sha256",
        "publication_source_sha256", "publication_proof_sha256",
        "attestation_sha256",
    }
    required = (
        common if type(version) is int and version == LEGACY_PUBLICATION_ATTESTATION_VERSION
        else common | {"review_receipt_sha256"}
        if type(version) is int and version == PUBLICATION_ATTESTATION_VERSION
        else set()
    )
    if not required or set(attestation) != required:
        return ["publication_attestation schema mismatch"]
    errors = []
    body = dict(attestation)
    claimed_hash = body.pop("attestation_sha256", None)
    if (attestation.get("kind") != PUBLICATION_ATTESTATION_KIND
            or claimed_hash != tr.sha256_json(body)):
        errors.append("publication_attestation hash/identity mismatch")
    expected = {
        "area": row.get("area"),
        "fid": row.get("fid"),
        "verdict": row.get("verdict"),
        "decision_sha256": tr.sha256_json(tr.decision_projection(row)),
        "evidence_sha256": tr.evidence_sha256(row),
        "packet_sha256": authority[2],
        "authority_kind": authority[0],
        "authority_sha256": authority[1],
        "publication_source_sha256": tr.sha256_json(publication_projection(row)),
    }
    bound = authority_wrapper(row)
    if (version == LEGACY_PUBLICATION_ATTESTATION_VERSION
            and bound is not None
            and bound[0] not in ("human_confirmation", "override_v2")):
        errors.append("current human authority cannot use a legacy publication attestation")
    if (version == PUBLICATION_ATTESTATION_VERSION
            and bound is not None
            and bound[0] not in ("human_confirmation_v2", "override_v3")):
        errors.append("legacy human authority cannot use a current publication attestation")
    if version == PUBLICATION_ATTESTATION_VERSION:
        expected["review_receipt_sha256"] = (
            bound[1].get("review_receipt_sha256") if bound is not None else None
        )
    for field, value in expected.items():
        if attestation.get(field) != value:
            errors.append(f"publication_attestation {field} mismatch")
    for field in (
        "packet_sha256", "authority_sha256", "resolver_run_id",
        "output_seal_sha256", "terminal_receipt_sha256",
        "publication_source_sha256", "publication_proof_sha256",
        "attestation_sha256",
    ):
        value = attestation.get(field)
        if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
            errors.append(f"publication_attestation {field} is invalid")
    if version == PUBLICATION_ATTESTATION_VERSION:
        review_hash = attestation.get("review_receipt_sha256")
        if review_hash is not None and (
                not isinstance(review_hash, str)
                or SHA256_RE.fullmatch(review_hash) is None):
            errors.append("publication_attestation review_receipt_sha256 is invalid")
    return errors


def authority_wrapper(row: object) -> tuple[str, dict] | None:
    if not isinstance(row, dict):
        return None
    confirmation = row.get("human_confirmation")
    if isinstance(confirmation, dict):
        version = confirmation.get("version")
        if type(version) is int and version == LEGACY_CONFIRMATION_VERSION:
            return "human_confirmation", confirmation
        if type(version) is int and version == CONFIRMATION_VERSION:
            return "human_confirmation_v2", confirmation
    override = row.get("override")
    if isinstance(override, dict):
        version = override.get("version")
        if type(version) is int and version == LEGACY_OVERRIDE_VERSION:
            return "override_v2", override
        if type(version) is int and version == OVERRIDE_VERSION:
            return "override_v3", override
    return None


def authority_receipt_path(tmp: str | Path, area: str, receipt_sha256: str) -> Path:
    if tr.area_slug(area) != area:
        raise ValueError("authority receipt requires a canonical separator-free area slug")
    if not isinstance(receipt_sha256, str) or SHA256_RE.fullmatch(receipt_sha256) is None:
        raise ValueError("authority receipt requires a lowercase SHA-256")
    root = Path(tmp).resolve()
    path = root / AUTHORITY_RECEIPT_DIR / area / f"{receipt_sha256}.json"
    current = root
    relative = path.relative_to(root)
    for part in relative.parts[:-1]:
        current = current / part
        if current.exists() and current.is_symlink():
            raise ValueError("authority receipt parent may not be a symlink")
    if path.exists() and path.is_symlink():
        raise ValueError("authority receipt path may not be a symlink")
    return path


def validate_authority_receipt(row: object, tmp: str | Path,
                               pending: dict[str, dict] | None = None) -> list[str]:
    bound = authority_wrapper(row)
    if bound is None:
        return []
    authority_kind, wrapper = bound
    claimed_hash = wrapper.get("authority_receipt_sha256")
    if not isinstance(claimed_hash, str) or SHA256_RE.fullmatch(claimed_hash) is None:
        return [f"{authority_kind}.authority_receipt_sha256 must be a lowercase SHA-256"]
    if pending is not None and claimed_hash in pending:
        receipt = copy.deepcopy(pending[claimed_hash])
    else:
        try:
            path = authority_receipt_path(
                tmp, str(row.get("area") or ""), claimed_hash
            )
            receipt = json.loads(path.read_text())
        except (OSError, ValueError, json.JSONDecodeError) as error:
            return [f"{authority_kind} authority receipt is unavailable: {error}"]
    legacy_required = {
        "version", "kind", "area", "fid", "authority_kind", "source_run_id",
        "source_run_path", "source_prepare_sha256", "primary_envelope_sha256",
        "primary_assignment_sha256", "packet_sha256", "reviewer", "date", "note",
        "authority_payload", "effective_decision_sha256",
        "effective_evidence_sha256", "receipt_sha256",
    }
    current_required = legacy_required | {
        "review_receipt_sha256", "review_receipt_path",
        "review_sheet_sha256", "review_sheet_path", "review_item_sha256",
    }
    version = receipt.get("version") if isinstance(receipt, dict) else None
    legacy = type(version) is int and version == LEGACY_AUTHORITY_RECEIPT_VERSION
    current = type(version) is int and version == AUTHORITY_RECEIPT_VERSION
    required = legacy_required if legacy else current_required if current else set()
    if not required or not isinstance(receipt, dict) or set(receipt) != required:
        return [f"{authority_kind} authority receipt schema mismatch"]
    if legacy and authority_kind not in ("human_confirmation", "override_v2"):
        return [f"{authority_kind} cannot use a legacy authority receipt"]
    if current and authority_kind not in ("human_confirmation_v2", "override_v3"):
        return [f"{authority_kind} cannot use a current authority receipt"]
    errors = []
    body = dict(receipt)
    receipt_hash = body.pop("receipt_sha256", None)
    if (receipt.get("kind") != AUTHORITY_RECEIPT_KIND
            or receipt_hash != claimed_hash
            or receipt_hash != tr.sha256_json(body)):
        errors.append(f"{authority_kind} authority receipt hash/identity mismatch")
    expected_payload = copy.deepcopy(wrapper)
    expected_payload.pop("authority_receipt_sha256", None)
    expected = {
        "area": row.get("area"),
        "fid": row.get("fid"),
        "authority_kind": authority_kind,
        "source_run_id": wrapper.get("source_run_id"),
        "packet_sha256": wrapper.get("packet_sha256"),
        "reviewer": wrapper.get("reviewer"),
        "date": wrapper.get("date"),
        "note": wrapper.get("note"),
        "authority_payload": expected_payload,
        "effective_decision_sha256": tr.sha256_json(tr.decision_projection(row)),
        "effective_evidence_sha256": tr.evidence_sha256(row),
    }
    if current:
        expected.update({
            "review_receipt_sha256": wrapper.get("review_receipt_sha256"),
            "review_sheet_sha256": wrapper.get("review_sheet_sha256"),
            "review_item_sha256": wrapper.get("review_item_sha256"),
        })
    for field, value in expected.items():
        if receipt.get(field) != value:
            errors.append(f"{authority_kind} authority receipt {field} mismatch")
    hash_fields = [
        "source_run_id", "source_prepare_sha256", "primary_envelope_sha256",
        "primary_assignment_sha256", "packet_sha256", "receipt_sha256",
    ]
    if current:
        hash_fields.extend([
            "review_receipt_sha256", "review_sheet_sha256", "review_item_sha256",
        ])
    for field in hash_fields:
        value = receipt.get(field)
        if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
            errors.append(f"{authority_kind} authority receipt {field} is invalid")
    path_fields = ["source_run_path"]
    if current:
        path_fields.extend(["review_receipt_path", "review_sheet_path"])
    for field in path_fields:
        value = receipt.get(field)
        if (not isinstance(value, str)
                or not Path(value).is_absolute()
                or str(Path(value).resolve()) != value):
            errors.append(f"{authority_kind} authority receipt {field} is invalid")
    return errors


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


def _decision_coherence_errors(decision: dict, label: str) -> list[str]:
    errors: list[str] = []
    verdict = decision.get("verdict")
    calls = []
    for axis in ("exists", "public", "serves"):
        value = decision.get(axis)
        if not isinstance(value, dict) or set(value) != {"call", "evidence"}:
            errors.append(f"{label} has malformed {axis}")
            continue
        calls.append(value.get("call"))
        if value.get("call") not in CALLS:
            errors.append(f"{label} {axis}.call is {value.get('call')!r}")
        if not isinstance(value.get("evidence"), str) or not value["evidence"].strip():
            errors.append(f"{label} {axis} has no evidence")
    if len(calls) == 3:
        if verdict == "KEEP" and calls != ["yes", "yes", "yes"]:
            errors.append(f"{label} KEEP does not have three yes axes")
        if verdict == "DROP" and "no" not in calls:
            errors.append(f"{label} DROP has no no axis")
        if verdict == "REVIEW" and ("no" in calls or "unclear" not in calls):
            errors.append(f"{label} REVIEW axes are inconsistent")
        if calls[0] == "no" and calls[1:] != ["n/a", "n/a"]:
            errors.append(f"{label} EXISTS=no requires PUBLIC/SERVES=n/a")
        if calls[0] != "no" and "n/a" in calls:
            errors.append(f"{label} uses n/a without EXISTS=no")
    if verdict in ("KEEP", "DROP") and decision.get("resolve_hint") is not None:
        errors.append(f"{label} binary verdict has a resolve_hint")
    if verdict == "REVIEW" and not decision.get("resolve_hint"):
        errors.append(f"{label} REVIEW has no resolve_hint")
    return errors


def machine_decision_projection(row: dict) -> tuple[dict | None, list[str]]:
    """Return the pre-human effective decision, retaining machine resolution."""
    errors: list[str] = []
    if not isinstance(row, dict):
        return None, ["row is not an object"]
    projected = copy.deepcopy(row)
    confirmation = projected.pop("human_confirmation", None)
    override = projected.pop("override", None)
    if confirmation is not None:
        if not isinstance(confirmation, dict):
            return None, ["human_confirmation must be an object"]
        version = confirmation.get("version")
        base_confirmation = {
            "version", "verdict", "by", "reviewer", "date", "note",
            "decision_sha256", "evidence_sha256", "packet_sha256", "source_run_id",
            "authority_receipt_sha256",
        }
        required_confirmation = (
            base_confirmation
            if type(version) is int and version == LEGACY_CONFIRMATION_VERSION
            else base_confirmation | {
                "review_receipt_sha256", "review_sheet_sha256",
                "review_item_sha256",
            }
            if type(version) is int and version == CONFIRMATION_VERSION
            else set()
        )
        if not required_confirmation or set(confirmation) != required_confirmation:
            errors.append(
                f"human_confirmation keys {sorted(confirmation)} != "
                f"{sorted(required_confirmation)}"
            )
        if type(version) is not int or version not in (
                LEGACY_CONFIRMATION_VERSION, CONFIRMATION_VERSION):
            errors.append(
                f"human_confirmation.version must be "
                f"{LEGACY_CONFIRMATION_VERSION} or {CONFIRMATION_VERSION}"
            )
        if confirmation.get("by") != "human":
            errors.append("human_confirmation.by must be human")
        if (not isinstance(confirmation.get("reviewer"), str)
                or tr.identity_key(confirmation["reviewer"]) != confirmation["reviewer"]):
            errors.append("human_confirmation.reviewer must be a canonical identity")
        try:
            _dt.date.fromisoformat(confirmation.get("date"))
        except (TypeError, ValueError):
            errors.append("human_confirmation.date must be YYYY-MM-DD")
        if not isinstance(confirmation.get("note"), str) or not confirmation["note"].strip():
            errors.append("human_confirmation.note must be a non-empty string")
        if confirmation.get("verdict") not in ("KEEP", "DROP"):
            errors.append("human_confirmation.verdict must be KEEP or DROP")
        if row.get("verdict") != confirmation.get("verdict"):
            errors.append("human_confirmation.verdict does not match the current verdict")
        confirmation_hash_fields = [
            "decision_sha256", "evidence_sha256", "packet_sha256", "source_run_id",
            "authority_receipt_sha256",
        ]
        if version == CONFIRMATION_VERSION:
            confirmation_hash_fields.extend([
                "review_receipt_sha256", "review_sheet_sha256",
                "review_item_sha256",
            ])
        for field in confirmation_hash_fields:
            if not isinstance(confirmation.get(field), str) or SHA256_RE.fullmatch(confirmation[field]) is None:
                errors.append(f"human_confirmation.{field} must be a lowercase SHA-256")
        expected_decision = tr.sha256_json(tr.decision_projection(projected))
        if confirmation.get("decision_sha256") != expected_decision:
            errors.append("human_confirmation.decision_sha256 does not match the confirmed decision")
        if confirmation.get("evidence_sha256") != tr.evidence_sha256(projected):
            errors.append("human_confirmation.evidence_sha256 does not match the confirmed evidence")
        if override is not None:
            errors.append("human_confirmation and override are mutually exclusive")
        if errors:
            return None, errors
    if override is None:
        return projected, errors
    if not isinstance(override, dict):
        return None, ["override must be an object"]
    if "version" in override and type(override.get("version")) is not int:
        return None, ["override.version must be an integer"]
    override_version = override.get("version")
    if type(override_version) is int and override_version in (
            LEGACY_OVERRIDE_VERSION, OVERRIDE_VERSION):
        label = f"override v{override_version}"
        required_override = {
            "version", "from_decision", "from_decision_sha256",
            "from_evidence_sha256", "to_decision_sha256", "to_evidence_sha256",
            "by", "reviewer", "date", "note", "packet_sha256", "source_run_id",
            "authority_receipt_sha256",
        }
        if override_version == OVERRIDE_VERSION:
            required_override.update({
                "review_receipt_sha256", "review_sheet_sha256",
                "review_item_sha256",
            })
        if set(override) != required_override:
            errors.append(
                f"{label} keys {sorted(override)} != {sorted(required_override)}"
            )
        if override.get("by") != "human":
            errors.append(f"{label} by must be human")
        if (not isinstance(override.get("reviewer"), str)
                or tr.identity_key(override["reviewer"]) != override["reviewer"]):
            errors.append(f"{label} reviewer must be a canonical identity")
        try:
            _dt.date.fromisoformat(override.get("date"))
        except (TypeError, ValueError):
            errors.append(f"{label} date must be YYYY-MM-DD")
        if not isinstance(override.get("note"), str) or not override["note"].strip():
            errors.append(f"{label} note must be a non-empty string")
        override_hash_fields = [
            "from_decision_sha256", "from_evidence_sha256",
            "to_decision_sha256", "to_evidence_sha256",
            "packet_sha256", "source_run_id", "authority_receipt_sha256",
        ]
        if override_version == OVERRIDE_VERSION:
            override_hash_fields.extend([
                "review_receipt_sha256", "review_sheet_sha256",
                "review_item_sha256",
            ])
        for field in override_hash_fields:
            if not isinstance(override.get(field), str) or SHA256_RE.fullmatch(override[field]) is None:
                errors.append(f"{label} {field} must be a lowercase SHA-256")
        from_decision = override.get("from_decision")
        if not isinstance(from_decision, dict):
            errors.append(f"{label} from_decision must be an object")
        else:
            decision_keys = set(from_decision)
            if not tr.REQUIRED_DECISION_FIELDS.issubset(decision_keys):
                errors.append(
                    f"{label} from_decision missing required fields "
                    f"{sorted(tr.REQUIRED_DECISION_FIELDS - decision_keys)}"
                )
            if not decision_keys.issubset(set(tr.DECISION_FIELDS)):
                errors.append(f"{label} from_decision contains non-decision fields")
            plain_errors, _ = validate_verdict_row(from_decision, allow_override=False)
            errors.extend(
                f"{label} source: {error}" for error in plain_errors
            )
            errors.extend(_decision_coherence_errors(from_decision, f"{label} source"))
            if override.get("from_decision_sha256") != tr.sha256_json(from_decision):
                errors.append(f"{label} source decision hash mismatch")
            if override.get("from_evidence_sha256") != tr.evidence_sha256(from_decision):
                errors.append(f"{label} source evidence hash mismatch")
            effective = tr.decision_projection(projected)
            if override.get("to_decision_sha256") != tr.sha256_json(effective):
                errors.append(f"{label} effective decision hash mismatch")
            if override.get("to_evidence_sha256") != tr.evidence_sha256(effective):
                errors.append(f"{label} effective evidence hash mismatch")
            if effective.get("verdict") == from_decision.get("verdict"):
                errors.append(f"{label} does not change the verdict")
            for field in ("fid", "area", "osm", "prior", "frames_used", "tags_cited", "coverage_gap"):
                if effective.get(field) != from_decision.get(field):
                    errors.append(f"{label} changes immutable field {field}")
            errors.extend(_decision_coherence_errors(effective, f"{label} effective"))
        if errors:
            return None, errors
        for field in tr.DECISION_FIELDS:
            projected.pop(field, None)
        projected.update(copy.deepcopy(from_decision))
        return projected, errors
    required = {"from", "by", "date", "note", "resolve_hint", "confidence_from"}
    if set(override) != required:
        errors.append(f"override keys {sorted(override)} != {sorted(required)}")
    if override.get("by") != "human":
        errors.append("override.by must be human")
    try:
        _dt.date.fromisoformat(override.get("date"))
    except (TypeError, ValueError):
        errors.append("override.date must be YYYY-MM-DD")
    if override.get("note") is not None and not isinstance(override.get("note"), str):
        errors.append("override.note must be a string or null")
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


def original_judge_projection(row: dict) -> tuple[dict | None, list[str]]:
    """Return the immutable primary decision beneath machine/human layers."""
    machine, errors = machine_decision_projection(row)
    if machine is None or errors:
        return None, errors
    if "trust_resolution" not in machine:
        return machine, []
    primary = tr.primary_decision(machine)
    if primary is None:
        return None, ["trust_resolution has no complete primary decision"]
    return primary, []


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
    if "human_confirmation" in row and not allow_override:
        errors.append("continuation rows may not contain human confirmations")
    if "trust_resolution" in row and not allow_override:
        errors.append("continuation rows may not contain machine resolutions")
    if not allow_override:
        allowed_plain = set(REQUIRED) | {"coverage_gap"}
        unexpected = sorted(set(row) - allowed_plain)
        if unexpected:
            errors.append(f"plain decision contains forbidden keys {unexpected}")

    fid = row.get("fid")
    if type(fid) is not int:
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
    source_frames = source.get("frames_used") if isinstance(source.get("frames_used"), list) else []
    if (source.get("prior") == "surveyed" and original_verdict == "DROP"
            and not any(frame in ("z3", "z3_naip") for frame in source_frames)):
        errors.append("DROP of a surveyed prior without Z3 in frames_used")

    machine, _machine_errors = machine_decision_projection(row)
    if isinstance(machine, dict) and "trust_resolution" in machine:
        full_packet = packet if isinstance(packet, dict) else None
        packet_identity = full_packet or {
            key: machine.get(key) for key in ("fid", "area", "osm", "prior")
        }

        def validate_plain(decision: dict) -> list[str]:
            plain_errors, _ = validate_verdict_row(
                decision, packet_identity, allow_override=False
            )
            return plain_errors

        expected_packet_hash = None
        wrapper_value = machine.get("trust_resolution")
        wrapper_version = (
            wrapper_value.get("version") if isinstance(wrapper_value, dict) else None
        )
        if full_packet is None or not isinstance(full_packet.get("tiles"), dict):
            if wrapper_version == tr.VERSION:
                errors.append(
                    "cannot verify trust_resolution packet bytes: "
                    "full packet with tiles is required"
                )
        else:
            try:
                expected_packet_hash = tr.packet_sha256(full_packet)
            except ValueError as error:
                errors.append(f"cannot verify trust_resolution packet bytes: {error}")
        errors.extend(tr.validate_persisted_resolution(
            machine, packet_identity, validate_plain,
            expected_packet_sha256=expected_packet_hash,
        ))

    authority_packet_hashes = []
    confirmation = row.get("human_confirmation")
    if isinstance(confirmation, dict):
        authority_packet_hashes.append(("human_confirmation", confirmation.get("packet_sha256")))
    override = row.get("override")
    if (isinstance(override, dict)
            and override.get("version") in (LEGACY_OVERRIDE_VERSION, OVERRIDE_VERSION)):
        authority_packet_hashes.append((
            f"override v{override.get('version')}", override.get("packet_sha256")
        ))
    if authority_packet_hashes:
        if not isinstance(packet, dict) or not isinstance(packet.get("tiles"), dict):
            errors.append(
                "cannot verify human authority packet bytes: full packet with tiles is required"
            )
        else:
            try:
                authority_packet_sha = tr.packet_sha256(packet)
            except ValueError as error:
                errors.append(f"cannot verify human authority packet bytes: {error}")
            else:
                for label, claimed_hash in authority_packet_hashes:
                    if claimed_hash != authority_packet_sha:
                        errors.append(f"{label}.packet_sha256 does not match the current packet")

    if isinstance(packet, dict):
        for key in ("fid", "area", "osm", "prior"):
            if row.get(key) != packet.get(key):
                errors.append(f"{key} does not match the current packet")
    if row.get("coverage_gap") not in (None, True):
        errors.append("coverage_gap must be true when present")
    return errors, projection
