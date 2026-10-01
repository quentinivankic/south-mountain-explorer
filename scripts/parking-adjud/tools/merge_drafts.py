#!/usr/bin/env python3
"""Validate one area's canonical judge drafts and optionally fold them into a
verdict store.

    python3 merge_drafts.py <store.json> <slug> [--judged DATE]
    python3 merge_drafts.py <store.json> <slug> --write --resolver-run PATH
    python3 merge_drafts.py <store.json> <slug> --confirm FID=VERDICT \
        --authority-run PATH --review-receipt PATH --reviewer ID --note TEXT
    python3 merge_drafts.py <store.json> <slug> --decide FID=VERDICT \
        --axis AXIS=CALL --axis-evidence AXIS=TEXT \
        --authority-run PATH --review-receipt PATH --reviewer ID --note TEXT

Validation checks canonical draft layout, `_pub.txt` coverage, full decision
schema, required Z3 evidence for surveyed-prior DROPs, packet/checkpoint bytes,
receipt-backed human authority, and the full transitive OSM holder component. Drafts cannot
stamp host-owned store fields such as `src`, dates, or geometry.

New human authority is always one-fid, review-bound, and receipt-bound.
`--confirm` records a same-verdict binary affirmation; `--decide` records a
coherent axis-explicit flip. Both require the canonical immutable review receipt
rendered from the named frozen authority run. Their helpers are pure planners;
the lock-holding CLI commits one journaled receipt → draft → checkpoint
transaction. A preflight failure changes no canonical target, and exact recovery
requires the original review receipt, run, request, reviewer, note, and date.
The unbound legacy `--set` CLI is disabled; its internal helper exists only to
replay historical fixtures and already-persisted legacy overrides.

`--write` is a separate operation, accepts exactly one area, and always requires
`--resolver-run`. That run must be terminal for the exact area/work directory,
fid set, dossier, and judge set; every row must carry current machine or
receipt-bound human authority. Merge uses publication fields bound by prepare
and adds a row reference to a separate canonical publication-proof registry
containing exact terminal artifacts and final decision/authority rows. Proof
registry and verdict store commit and recover through one journaled transaction.
Store entries add host-owned
`area`, `src`, `judged`, `lat`, `lon`, `rings`, and `name`. Existing direct-human
`user*` holders are preserved only when complete effective decisions agree; all
known aliases are rewritten together.
"""
from __future__ import annotations

import base64
import copy
import datetime as _dt
import fcntl
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.normpath(os.path.join(_HERE, "..", ".."))
for _path in (_HERE, _SCRIPTS):
    if _path not in sys.path:
        sys.path.insert(0, _path)
from judge_validation import (  # noqa: E402
    AUTHORITY_RECEIPT_DIR,
    AUTHORITY_RECEIPT_KIND,
    AUTHORITY_RECEIPT_VERSION,
    CONFIRMATION_VERSION,
    OVERRIDE_VERSION,
    PUBLICATION_ATTESTATION_KIND,
    PUBLICATION_ATTESTATION_VERSION,
    authority_receipt_path,
    authority_wrapper,
    current_authority,
    publication_projection,
    canonical_draft_files,
    load_canonical_drafts,
    validate_authority_receipt,
    validate_publication_attestation,
    validate_verdict_row,
)
import dossier_output  # noqa: E402
import judge_packets  # noqa: E402
import resolve_trust as resolver  # noqa: E402
import review_evidence as review  # noqa: E402
import trust_resolution as tr  # noqa: E402
import trusted_filesystem as trusted_fs  # noqa: E402
import _parking_verdict_source as verdict_source  # noqa: E402

PADJ_TMP = os.environ.get("PADJ_TMP") or os.path.join(_HERE, "..", "work")
REQ = ["fid", "osm", "prior", "verdict", "exists", "public", "serves", "confidence"]
VERDICTS = ("KEEP", "DROP", "REVIEW")
CONFIDENCE = ("certain", "strong", "leaning")
CALLS = ("yes", "no", "unclear", "n/a")


def _terminal_resolver_errors(tmp: Path, slug: str, drafts: list[dict],
                              store: dict, run_path: Path | None,
                              generation_capture) -> tuple[list[str], dict | None]:
    del store
    if run_path is None:
        return [
            f"{slug}: store write requires --resolver-run with a terminal authoritative run"
        ], None
    try:
        prepare = resolver.load_prepare_document(
            run_path / "prepare.json", expected_area=slug
        )
        if prepare["tmp"] != str(tmp.resolve()):
            raise ValueError("resolver run targets a different work directory")
        publication = prepare["source"]["publication"]
        resolver.require_current_source_generation(prepare, generation_capture)
        tmp_root = tmp.resolve()
        public_set_path = resolver._trusted_artifact_path(
            tmp_root / publication["public_set_path"], tmp_root
        )
        if (not public_set_path.is_file()
                or resolver._sha(public_set_path.read_bytes())
                != publication["public_set_sha256"]):
            raise ValueError("publication judge set changed after resolver prepare")
        expected_fids = sorted(row.get("fid") for row in drafts if isinstance(row, dict))
        prepared_fids = sorted(item["fid"] for item in prepare["items"])
        if prepared_fids != expected_fids:
            raise ValueError("resolver run fid set differs from publication drafts")
        status, _ = resolver.evaluate_locked(run_path, generation_capture)
        if status.get("state") != "APPLIED":
            raise ValueError(f"resolver run state is {status.get('state')}")
        nonterminal = [
            chunk for chunk in status.get("chunks", [])
            if chunk.get("source_state") not in ("applied", "preserved")
        ]
        if nonterminal:
            raise ValueError(f"resolver run has nonterminal chunks {nonterminal}")
        for row in drafts:
            if not isinstance(row, dict):
                continue
            if current_authority(row) is None:
                raise ValueError(
                    f"fid {row.get('fid')} has no current machine or receipt-bound human authority"
                )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return [f"{slug}: resolver publication gate failed: {error}"], None
    return [], prepare


def draft_files(tmp: Path, slug: str) -> list[Path]:
    return canonical_draft_files(tmp, slug)


def load_draft(tmp: Path, slug: str) -> list[dict] | None:
    return load_canonical_drafts(tmp, slug)


def load_staged_draft(tmp: Path, slug: str,
                      staged_drafts: dict[Path, list[dict]]) -> list[dict] | None:
    files = draft_files(tmp, slug)
    if not files:
        return None
    rows = []
    for path in files:
        rows.extend(copy.deepcopy(staged_drafts[path]) if path in staged_drafts
                    else json.loads(path.read_text()))
    return rows


def _packet_bound(row: object) -> bool:
    return isinstance(row, dict) and (
        "trust_resolution" in row
        or "human_confirmation" in row
        or (isinstance(row.get("override"), dict)
            and type(row["override"].get("version")) is int
            and row["override"].get("version") in (2, OVERRIDE_VERSION))
    )


def _bound_packets(tmp: Path, slug: str, drafts: list[dict],
                   staged_drafts: dict[Path, list[dict]] | None = None,
                   terminal_prepare: dict | None = None,
                   terminal_run: Path | None = None) -> tuple[dict[int, dict], list[str]]:
    """Bind machine and human authority rows to packet bytes + checkpoint."""
    staged_drafts = staged_drafts or {}
    if not any(_packet_bound(row) for row in drafts):
        return {}, []
    issues = []
    try:
        if terminal_prepare is not None and terminal_run is not None:
            packets = {
                item["fid"]: json.loads(
                    (terminal_run / item["packet_path"]).read_text()
                )
                for item in terminal_prepare["items"]
            }
        else:
            packet_path = resolver._trusted_artifact_path(
                tmp.resolve() / f"{slug}_packets.json", tmp.resolve()
            )
            raw_packets = json.loads(packet_path.read_text())
            packets = {int(fid): packet for fid, packet in raw_packets.items()}
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return {}, [f"{slug}: bound authority requires valid frozen packets: {error}"]
    files = draft_files(tmp, slug)
    numbered = re.compile(rf"^{re.escape(slug)}_verdict_draft_(\d{{2}})\.json$")
    for fallback, path in enumerate(files):
        path = resolver._trusted_artifact_path(path, tmp.resolve())
        rows = (copy.deepcopy(staged_drafts[path]) if path in staged_drafts
                else json.loads(path.read_text()))
        if not any(_packet_bound(row) for row in rows):
            continue
        match = numbered.fullmatch(path.name)
        index = int(match.group(1)) if match else fallback
        try:
            chunk = [packets[row["fid"]] for row in rows]
        except (KeyError, TypeError) as error:
            issues.append(f"{slug}: {path.name} has no full packet for {error}")
            continue
        state = judge_packets.inspect_rows(chunk, rows, path.name)
        decision_input, missing = judge_packets.decision_fingerprint(chunk)
        checkpoint = resolver._trusted_artifact_path(
            tmp.resolve() / f"{slug}_checkpoint_{index:02d}.json", tmp.resolve()
        )
        if state["status"] != "complete":
            issues.append(f"{slug}: {path.name} resolution state invalid: {state['errors']}")
            continue
        if missing or not decision_input:
            issues.append(f"{slug}: {path.name} missing packet/tile inputs: {missing}")
            continue
        try:
            manifest = json.loads(checkpoint.read_text())
        except (OSError, json.JSONDecodeError) as error:
            issues.append(f"{slug}: cannot read {checkpoint.name}: {error}")
            continue
        for error in judge_packets.validate_manifest(
                manifest, slug, index, chunk, decision_input, state):
            issues.append(f"{slug}: {checkpoint.name}: {error}")
    return packets, issues


def _dump_atomic(path: Path, rows: list[dict]) -> None:
    """Write one host-owned draft through descriptor-safe atomic replacement."""
    data = json.dumps(
        rows, indent=1, ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    resolver._atomic_bytes(path, data)


def set_verdict(e: dict, to: str, note: str | None, today: str) -> str:
    """Apply one human decision to one draft entry, in place, and say what
    happened. The judge's ORIGINAL call is what `override.from` records, so a
    second flip keeps it, and flipping back to the judge's call removes the
    override altogether (nothing was overridden in the end)."""
    fid, was = e.get("fid"), e.get("verdict")
    ov = e.get("override")
    if was == to:
        return f"  #{fid}: already {to}, nothing to set"
    if isinstance(ov, dict) and "version" in ov:
        return f"  !! #{fid}: versioned authority cannot be changed by legacy --set"
    e.pop("human_confirmation", None)
    # A human sending a lot (back) to REVIEW owes the next reader a hint; the
    # --note is it. Any other verdict clears the judge's hint (kept in override).
    hint_for_to = (note or "human asked for a second look") if to == "REVIEW" else None
    if ov:
        if ov.get("from") == to:
            e["verdict"] = to
            e["confidence"] = ov.get("confidence_from") or e.get("confidence")
            e["resolve_hint"] = ov.get("resolve_hint")
            del e["override"]
            return f"  #{fid}: {was} -> {to} (back to the judge's call; override removed)"
        ov.update({"date": today, "note": note})
        e["verdict"] = to
        e["confidence"] = "strong"
        e["resolve_hint"] = hint_for_to
        return f"  #{fid}: {was} -> {to} (human override revised; judge said {ov.get('from')})"
    e["override"] = {
        "from": was, "by": "human", "date": today, "note": note,
        "resolve_hint": e.get("resolve_hint"),
        "confidence_from": e.get("confidence"),
    }
    e["verdict"] = to
    e["confidence"] = "strong"
    e["resolve_hint"] = hint_for_to
    return f"  #{fid}: {was} -> {to} (human override)"


def apply_overrides(tmp: Path, slug: str, sets: dict[int, str], note: str | None,
                    today: str, staged_drafts: dict[Path, list[dict]]) -> list[str]:
    """Plan replay of legacy override semantics into caller-owned staging."""
    lines: list[str] = []
    pending = dict(sets)
    for path in draft_files(tmp, slug):
        rows = (copy.deepcopy(staged_drafts[path]) if path in staged_drafts
                else json.load(open(path)))
        changed = False
        for e in rows:
            fid = e.get("fid")
            if fid not in pending:
                continue
            before = json.dumps(e, sort_keys=True)
            lines.append(set_verdict(e, pending.pop(fid), note, today) + f" [{path.name}]")
            changed = changed or json.dumps(e, sort_keys=True) != before
        if changed:
            staged_drafts[path] = rows
    for fid, to in pending.items():
        lines.append(f"  !! #{fid}: not in any {slug} draft, cannot set {to}")
    return lines


def _load_authority_prepare(authority_run: Path, slug: str) -> dict:
    path = authority_run / "prepare.json"
    if not path.is_file():
        raise ValueError(f"authority run has no prepare.json: {authority_run}")
    prepare = resolver.load_prepare_document(path, expected_area=slug)
    if authority_run.name != prepare["run_id"]:
        raise ValueError("authority run directory name does not match run_id")
    return prepare


def _authority_source_state_errors(prepare: dict, tmp: Path,
                                   generation_capture=None) -> list[str]:
    """Validate an unchanged live source before a planner-only helper call."""
    errors = []
    root = tmp.resolve()
    try:
        capture = (
            dossier_output.capture_generation(root, prepare["area"])
            if generation_capture is None else generation_capture
        )
        resolver.require_current_source_generation(prepare, capture)
    except (OSError, ValueError, KeyError, TypeError) as error:
        errors.append(f"source generation changed after authority prepare: {error}")
    try:
        packet_path = resolver._trusted_artifact_path(
            root / prepare["source"]["packets_path"], root
        )
        if (resolver._sha(resolver._read_bytes_nofollow(packet_path))
                != prepare["source"]["packets_file_sha256"]):
            errors.append(
                f"{prepare['source']['packets_path']} changed after authority prepare"
            )
    except (OSError, ValueError):
        errors.append(
            f"{prepare['source']['packets_path']} changed after authority prepare"
        )
    for source in prepare["source"]["drafts"]:
        for path_field, hash_field in (
            ("path", "file_sha256"),
            ("checkpoint_path", "checkpoint_sha256"),
        ):
            try:
                target = resolver._trusted_artifact_path(
                    root / source[path_field], root
                )
                if (resolver._sha(resolver._read_bytes_nofollow(target))
                        != source[hash_field]):
                    errors.append(
                        f"{source[path_field]} changed after authority prepare"
                    )
            except (OSError, ValueError):
                errors.append(f"{source[path_field]} changed after authority prepare")
    publication = prepare["source"]["publication"]
    for path_field, hash_field in (
        ("public_set_path", "public_set_sha256"),
    ):
        try:
            target = resolver._trusted_artifact_path(
                root / publication[path_field], root
            )
            if (resolver._sha(resolver._read_bytes_nofollow(target))
                    != publication[hash_field]):
                errors.append(
                    f"{publication[path_field]} changed after authority prepare"
                )
        except (OSError, ValueError):
            errors.append(
                f"{publication[path_field]} changed after authority prepare"
            )
    return errors


def _authority_frozen_source_drafts(tmp: Path, slug: str,
                                    authority_run: Path,
                                    prepare: dict) -> dict[Path, list[dict]]:
    """Load planner before-images only from the validated frozen prepare run."""
    root = tmp.resolve()
    run_root = authority_run.resolve()
    if (prepare.get("area") != slug or prepare.get("tmp") != str(root)):
        raise ValueError("authority run targets a different area or work directory")
    drafts: dict[Path, list[dict]] = {}
    for source in prepare["source"]["drafts"]:
        target = resolver._trusted_artifact_path(root / source["path"], root)
        frozen = resolver._trusted_artifact_path(
            run_root / source["frozen_path"], run_root
        )
        source_bytes = resolver._read_bytes_nofollow(frozen)
        if resolver._sha(source_bytes) != source["file_sha256"]:
            raise ValueError(
                f"authority frozen draft {source['chunk']:02d} changed"
            )
        rows = json.loads(source_bytes)
        if not isinstance(rows, list):
            raise ValueError(
                f"authority frozen draft {source['chunk']:02d} is not a list"
            )
        drafts[target] = rows
    return drafts


def _review_bindings(review_context: dict, slug: str, authority_run: Path,
                     prepare: dict, prepared_item: dict) -> dict:
    if not isinstance(review_context, dict):
        raise ValueError("current human authority requires validated review evidence")
    receipt = review_context.get("receipt")
    item = review_context.get("item")
    if (not isinstance(receipt, dict) or not isinstance(item, dict)
            or review_context.get("prepare") != prepare
            or receipt.get("area") != slug
            or receipt.get("source_run_id") != prepare.get("run_id")
            or receipt.get("source_run_path") != str(authority_run.resolve())
            or item.get("fid") != prepared_item.get("fid")
            or item.get("prepared_item_sha256") != tr.sha256_json(prepared_item)
            or item.get("packet_sha256") != prepared_item.get("packet_sha256")
            or item.get("primary_envelope_sha256")
            != prepared_item.get("primary", {}).get("envelope_sha256")):
        raise ValueError("review evidence differs from the selected frozen item")
    expected_paths = review.artifact_paths(
        prepare["tmp"], slug, prepare["run_id"]
    )
    if (review_context.get("receipt_path") != expected_paths["receipt"]
            or review_context.get("sheet_path") != expected_paths["sheet"]
            or receipt.get("sheet_path") != str(expected_paths["sheet"])):
        raise ValueError("review evidence paths are noncanonical")
    return {
        "review_receipt_sha256": receipt["receipt_sha256"],
        "review_receipt_path": str(expected_paths["receipt"]),
        "review_sheet_sha256": receipt["sheet_sha256"],
        "review_sheet_path": str(expected_paths["sheet"]),
        "review_item_sha256": item["review_item_sha256"],
    }


def _build_authority_receipt(tmp: Path, slug: str, authority_run: Path,
                             prepare: dict, prepared_item: dict, row: dict,
                             authority_kind: str, wrapper: dict,
                             review_context: dict) -> tuple[str, dict]:
    expected_assignment = resolver.expected_primary_assignment(prepare, prepared_item)
    primary = prepared_item["primary"]
    payload = copy.deepcopy(wrapper)
    payload.pop("authority_receipt_sha256", None)
    review_bindings = _review_bindings(
        review_context, slug, authority_run, prepare, prepared_item
    )
    body = {
        "version": AUTHORITY_RECEIPT_VERSION,
        "kind": AUTHORITY_RECEIPT_KIND,
        "area": slug,
        "fid": row["fid"],
        "authority_kind": authority_kind,
        "source_run_id": prepare["run_id"],
        "source_run_path": str(authority_run.resolve()),
        "source_prepare_sha256": resolver._sha(
            resolver._read_bytes_nofollow(
                resolver._trusted_artifact_path(
                    authority_run.resolve() / "prepare.json",
                    authority_run.resolve(),
                )
            )
        ),
        "primary_envelope_sha256": primary["envelope_sha256"],
        "primary_assignment_sha256": tr.sha256_json(expected_assignment),
        "packet_sha256": wrapper["packet_sha256"],
        "reviewer": wrapper["reviewer"],
        "date": wrapper["date"],
        "note": wrapper["note"],
        "authority_payload": payload,
        "effective_decision_sha256": tr.sha256_json(tr.decision_projection(row)),
        "effective_evidence_sha256": tr.evidence_sha256(row),
        **review_bindings,
    }
    receipt_sha = tr.sha256_json(body)
    return receipt_sha, {**body, "receipt_sha256": receipt_sha}


def _validate_receipt_source(receipt: dict, row: dict, packet: dict) -> list[str]:
    errors = []
    try:
        authority_run = Path(receipt["source_run_path"]).resolve()
        if str(authority_run) != receipt.get("source_run_path"):
            errors.append("authority receipt source run path is noncanonical")
        prepare = _load_authority_prepare(authority_run, row["area"])
        if receipt.get("source_run_id") != prepare.get("run_id"):
            errors.append("authority receipt source run id mismatch")
        prepare_path = resolver._trusted_artifact_path(
            authority_run / "prepare.json", authority_run
        )
        prepare_sha = resolver._sha(
            resolver._read_bytes_nofollow(prepare_path)
        )
        if prepare_sha != receipt.get("source_prepare_sha256"):
            errors.append("authority receipt source prepare bytes changed")
        prepared_item = next(
            (item for item in prepare["items"] if item.get("fid") == row.get("fid")),
            None,
        )
        if prepared_item is None:
            errors.append("authority receipt source run has no matching item")
            return errors
        primary = prepared_item["primary"]
        if primary.get("envelope_sha256") != receipt.get("primary_envelope_sha256"):
            errors.append("authority receipt primary envelope mismatch")
        expected_assignment = resolver.expected_primary_assignment(prepare, prepared_item)
        if tr.sha256_json(expected_assignment) != receipt.get("primary_assignment_sha256"):
            errors.append("authority receipt primary assignment mismatch")
        bound = authority_wrapper(row)
        source_decision = (
            bound[1].get("from_decision")
            if bound is not None and bound[0].startswith("override_v")
            else {key: copy.deepcopy(value) for key, value in row.items()
                  if key != "human_confirmation"}
        )
        errors.extend(_authority_binding_errors(
            prepare, prepared_item, packet, source_decision
        ))
        if receipt.get("version") == AUTHORITY_RECEIPT_VERSION:
            review_context = resolver.load_review_receipt(
                Path(receipt["review_receipt_path"]), authority_run,
                row["area"], expected_fid=row["fid"],
            )
            bindings = _review_bindings(
                review_context, row["area"], authority_run, prepare,
                prepared_item,
            )
            for field, expected in bindings.items():
                if receipt.get(field) != expected:
                    errors.append(f"authority receipt {field} mismatch")
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        errors.append(f"authority receipt source run invalid: {error}")
    return errors


AUTHORITY_TRANSACTION_VERSION = 2
AUTHORITY_TRANSACTION_KIND = "parking-human-authority-transaction"
AUTHORITY_TRANSACTION_IDENTITY_KIND = (
    "parking-human-authority-transaction-identity"
)
AUTHORITY_REQUEST_KIND = "parking-human-authority-request"
AUTHORITY_ABSENCE_BACKUP_KIND = "parking-human-authority-absence-backup"


@dataclass(frozen=True)
class _AuthorityPlan:
    tmp: Path
    area: str
    chunk: int
    fid: int
    authority_kind: str
    authority_run: Path
    prepare: dict
    source_generation: dict
    prepared_item: dict
    source: dict
    packet: dict
    request: dict
    receipt: dict
    before_bytes: dict[str, bytes | None]
    after_bytes: dict[str, bytes]
    backup_bytes: dict[str, bytes]
    identity: dict
    transaction_id: str
    paths: dict[str, Path]
    journal: dict
    recovering: bool


def _canonical_authority_request(context: dict) -> dict:
    required = {
        "version", "kind", "authority_kind", "verdict", "axis_calls",
        "axis_evidence", "reviewer", "date", "note",
        "review_receipt_sha256", "review_sheet_sha256", "review_item_sha256",
    }
    if not isinstance(context, dict) or set(context) != required:
        raise ValueError("authority request context is malformed")
    authority_kind = context.get("authority_kind")
    if (type(context.get("version")) is not int
            or context["version"] != AUTHORITY_TRANSACTION_VERSION
            or context.get("kind") != AUTHORITY_REQUEST_KIND
            or authority_kind not in ("human_confirmation_v2", "override_v3")
            or context.get("verdict") not in ("KEEP", "DROP")
            or tr.identity_key(context.get("reviewer")) != context.get("reviewer")
            or not isinstance(context.get("note"), str)
            or not context["note"].strip()):
        raise ValueError("authority request identity is invalid")
    try:
        _dt.date.fromisoformat(context["date"])
    except (TypeError, ValueError) as error:
        raise ValueError("authority request date is invalid") from error
    calls = context.get("axis_calls")
    evidence = context.get("axis_evidence")
    if (not isinstance(calls, dict) or not isinstance(evidence, dict)
            or set(calls) != set(evidence)):
        raise ValueError("authority request axes are malformed")
    if authority_kind == "human_confirmation_v2" and calls:
        raise ValueError("confirmation authority cannot carry axis changes")
    if authority_kind == "override_v3" and not calls:
        raise ValueError("bound decision authority requires axis changes")
    for axis, call in calls.items():
        if (axis not in ("exists", "public", "serves")
                or call not in ("yes", "no", "unclear", "n/a")
                or not isinstance(evidence[axis], str)
                or not evidence[axis].strip()):
            raise ValueError(f"authority request axis {axis!r} is invalid")
    for field in (
        "review_receipt_sha256", "review_sheet_sha256", "review_item_sha256",
    ):
        value = context.get(field)
        if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
            raise ValueError(f"authority request {field} is invalid")
    return {
        "version": AUTHORITY_TRANSACTION_VERSION,
        "kind": AUTHORITY_REQUEST_KIND,
        "authority_kind": authority_kind,
        "verdict": context["verdict"],
        "axis_calls": {key: calls[key] for key in sorted(calls)},
        "axis_evidence": {key: evidence[key] for key in sorted(evidence)},
        "reviewer": context["reviewer"],
        "date": context["date"],
        "note": context["note"],
        "review_receipt_sha256": context["review_receipt_sha256"],
        "review_sheet_sha256": context["review_sheet_sha256"],
        "review_item_sha256": context["review_item_sha256"],
    }


def _authority_transaction_paths(tmp: Path, area: str, chunk: int, fid: int,
                                 receipt_sha: str,
                                 transaction_id: str) -> dict[str, Path]:
    root = tmp.resolve()
    transaction_root = root / ".human-authority-transactions"
    stem = f"{area}.chunk-{chunk:02d}.fid-{fid}.{transaction_id}"
    paths = {
        "receipt": authority_receipt_path(root, area, receipt_sha).absolute(),
        "draft": root / f"{area}_verdict_draft_{chunk:02d}.json",
        "checkpoint": root / f"{area}_checkpoint_{chunk:02d}.json",
        "receipt_stage": transaction_root / "staged" / f"{stem}.receipt.json",
        "draft_stage": transaction_root / "staged" / f"{stem}.draft.json",
        "checkpoint_stage": transaction_root / "staged" / f"{stem}.checkpoint.json",
        "receipt_backup": transaction_root / "backups" / f"{stem}.receipt.json",
        "draft_backup": transaction_root / "backups" / f"{stem}.draft.json",
        "checkpoint_backup": transaction_root / "backups" / f"{stem}.checkpoint.json",
        "journal": tr.authority_journal_path(root, area),
        "archive": transaction_root / "Archive" / f"{stem}.journal.json",
    }
    return {
        key: resolver._trusted_artifact_path(path, root)
        for key, path in paths.items()
    }


def _authority_absence_backup(target: Path, transaction_id: str) -> bytes:
    return resolver._json_bytes({
        "version": AUTHORITY_TRANSACTION_VERSION,
        "kind": AUTHORITY_ABSENCE_BACKUP_KIND,
        "target": str(target),
        "transaction_id": transaction_id,
    })


def _assemble_authority_plan(
        tmp: Path, area: str, authority_run: Path, prepare: dict,
        generation_capture, prepared_item: dict, source: dict, packet: dict,
        request: dict,
        receipt: dict, draft_before: bytes, checkpoint_before: bytes,
        draft_after: bytes, checkpoint_after: bytes,
        receipt_before: bytes | None, recovering: bool) -> _AuthorityPlan:
    request = _canonical_authority_request(request)
    source_generation = resolver.require_current_source_generation(
        prepare, generation_capture
    )
    chunk = source["chunk"]
    fid = prepared_item["fid"]
    receipt_after = resolver._json_bytes(receipt)
    receipt_sha = receipt["receipt_sha256"]
    root = tmp.resolve()
    fixed_paths = _authority_transaction_paths(
        root, area, chunk, fid, receipt_sha, "fixed"
    )
    targets = {
        prefix: str(fixed_paths[prefix])
        for prefix in ("receipt", "draft", "checkpoint")
    }
    before_bytes = {
        "receipt": receipt_before,
        "draft": draft_before,
        "checkpoint": checkpoint_before,
    }
    after_bytes = {
        "receipt": receipt_after,
        "draft": draft_after,
        "checkpoint": checkpoint_after,
    }
    before_hashes = {
        key: resolver._sha(value) if value is not None else None
        for key, value in before_bytes.items()
    }
    after_hashes = {
        key: resolver._sha(value) for key, value in after_bytes.items()
    }
    requested_decision = {
        "verdict": request["verdict"],
        "axis_calls": request["axis_calls"],
    }
    requested_evidence = {
        "axis_evidence": request["axis_evidence"],
    }
    identity = {
        "version": AUTHORITY_TRANSACTION_VERSION,
        "kind": AUTHORITY_TRANSACTION_IDENTITY_KIND,
        "area": area,
        "chunk": chunk,
        "fid": fid,
        "authority_kind": request["authority_kind"],
        "targets": targets,
        "before_sha256": before_hashes,
        "after_sha256": after_hashes,
        "source": {
            "run_id": prepare["run_id"],
            "run_path": str(authority_run.resolve()),
            "prepare_sha256": receipt["source_prepare_sha256"],
            "packet_sha256": receipt["packet_sha256"],
            "primary_assignment_sha256": receipt[
                "primary_assignment_sha256"
            ],
            "primary_envelope_sha256": receipt["primary_envelope_sha256"],
            "review_receipt_sha256": receipt["review_receipt_sha256"],
            "review_sheet_sha256": receipt["review_sheet_sha256"],
            "review_item_sha256": receipt["review_item_sha256"],
        },
        "human": {
            "reviewer": request["reviewer"],
            "date": request["date"],
            "note": request["note"],
        },
        "request": {
            "verdict": request["verdict"],
            "axis_calls": copy.deepcopy(request["axis_calls"]),
            "axis_evidence": copy.deepcopy(request["axis_evidence"]),
            "review_receipt_sha256": request["review_receipt_sha256"],
            "review_sheet_sha256": request["review_sheet_sha256"],
            "review_item_sha256": request["review_item_sha256"],
            "requested_decision_sha256": tr.sha256_json(requested_decision),
            "requested_evidence_sha256": tr.sha256_json(requested_evidence),
        },
        "effective": {
            "decision_sha256": receipt["effective_decision_sha256"],
            "evidence_sha256": receipt["effective_evidence_sha256"],
        },
    }
    transaction_id = tr.sha256_json(identity)
    paths = _authority_transaction_paths(
        root, area, chunk, fid, receipt_sha, transaction_id
    )
    backup_bytes = {
        "receipt": (
            receipt_before if receipt_before is not None
            else _authority_absence_backup(paths["receipt"], transaction_id)
        ),
        "draft": draft_before,
        "checkpoint": checkpoint_before,
    }
    journal = {
        "version": AUTHORITY_TRANSACTION_VERSION,
        "kind": AUTHORITY_TRANSACTION_KIND,
        "transaction_id": transaction_id,
        "identity": identity,
        "paths": {key: str(value) for key, value in paths.items()},
    }
    return _AuthorityPlan(
        tmp=root,
        area=area,
        chunk=chunk,
        fid=fid,
        authority_kind=request["authority_kind"],
        authority_run=authority_run.resolve(),
        prepare=copy.deepcopy(prepare),
        source_generation=copy.deepcopy(source_generation),
        prepared_item=copy.deepcopy(prepared_item),
        source=copy.deepcopy(source),
        packet=copy.deepcopy(packet),
        request=request,
        receipt=copy.deepcopy(receipt),
        before_bytes=before_bytes,
        after_bytes=after_bytes,
        backup_bytes=backup_bytes,
        identity=identity,
        transaction_id=transaction_id,
        paths=paths,
        journal=journal,
        recovering=recovering,
    )


def _build_authority_plan(staged_drafts: dict[Path, list[dict]],
                          staged_receipts: dict[str, dict],
                          tmp: Path, slug: str, authority_run: Path,
                          prepare: dict, request: dict,
                          generation_capture) -> _AuthorityPlan:
    if tr.area_slug(slug) != slug:
        raise ValueError("authority transaction requires a canonical area slug")
    if len(staged_drafts) != 1 or len(staged_receipts) != 1:
        raise ValueError(
            "authority transaction requires exactly one draft and one receipt"
        )
    root = tmp.resolve()
    run_root = authority_run.resolve()
    if prepare.get("area") != slug or prepare.get("tmp") != str(root):
        raise ValueError("authority run targets a different area or work directory")
    request = _canonical_authority_request(request)
    receipt_sha, receipt = next(iter(staged_receipts.items()))
    if (receipt.get("receipt_sha256") != receipt_sha
            or receipt.get("authority_kind") != request["authority_kind"]
            or receipt.get("reviewer") != request["reviewer"]
            or receipt.get("date") != request["date"]
            or receipt.get("note") != request["note"]
            or receipt.get("review_receipt_sha256")
            != request["review_receipt_sha256"]
            or receipt.get("review_sheet_sha256")
            != request["review_sheet_sha256"]
            or receipt.get("review_item_sha256")
            != request["review_item_sha256"]):
        raise ValueError("authority receipt differs from the current human request")
    prepared_item = next(
        (item for item in prepare["items"]
         if item.get("fid") == receipt.get("fid")),
        None,
    )
    if prepared_item is None:
        raise ValueError("authority run has no item for the requested fid")
    source = next(
        (value for value in prepare["source"]["drafts"]
         if value["chunk"] == prepared_item["chunk"]),
        None,
    )
    if source is None:
        raise ValueError("authority run has no source chunk for the requested fid")
    draft_target, staged_rows = next(iter(staged_drafts.items()))
    draft_target = resolver._trusted_artifact_path(draft_target, root)
    expected_draft_target = resolver._trusted_artifact_path(
        root / source["path"], root
    )
    if draft_target != expected_draft_target:
        raise ValueError("staged authority draft is not the prepared source chunk")
    if source["path"] != f"{slug}_verdict_draft_{source['chunk']:02d}.json":
        raise ValueError("authority transaction requires a numbered canonical draft")

    frozen_draft = resolver._trusted_artifact_path(
        run_root / source["frozen_path"], run_root
    )
    frozen_checkpoint = resolver._trusted_artifact_path(
        run_root / source["checkpoint_frozen_path"], run_root
    )
    draft_before = resolver._read_bytes_nofollow(frozen_draft)
    checkpoint_before = resolver._read_bytes_nofollow(frozen_checkpoint)
    if (resolver._sha(draft_before) != source["file_sha256"]
            or resolver._sha(checkpoint_before) != source["checkpoint_sha256"]):
        raise ValueError("authority frozen before-images changed")
    before_rows = json.loads(draft_before)
    if (not isinstance(before_rows, list)
            or not any(row.get("fid") == prepared_item["fid"]
                       for row in before_rows if isinstance(row, dict))):
        raise ValueError("authority frozen draft does not contain the requested fid")
    draft_after = json.dumps(
        staged_rows, indent=1, ensure_ascii=False, allow_nan=False
    ).encode("utf-8")
    checkpoint_value = json.loads(checkpoint_before)
    if not isinstance(checkpoint_value, dict):
        raise ValueError("authority frozen checkpoint is not an object")
    checkpoint_value["draft_sha256"] = resolver._sha(draft_after)
    checkpoint_after = resolver._json_bytes(checkpoint_value)

    packet_path = resolver._trusted_artifact_path(
        run_root / prepared_item["packet_path"], run_root
    )
    packet = json.loads(resolver._read_bytes_nofollow(packet_path))
    if not isinstance(packet, dict):
        raise ValueError("authority frozen packet is not an object")
    receipt_target = resolver._trusted_artifact_path(
        authority_receipt_path(root, slug, receipt_sha), root
    )
    receipt_after = resolver._json_bytes(receipt)
    try:
        current_receipt = resolver._read_bytes_nofollow(receipt_target)
    except FileNotFoundError:
        current_receipt = None
    if current_receipt not in (None, receipt_after):
        raise ValueError(
            "existing authority receipt differs from the exact after-image"
        )

    journal_path = tr.authority_journal_path(root, slug)
    recovering = journal_path.exists()
    plan = _assemble_authority_plan(
        root, slug, run_root, prepare, generation_capture,
        prepared_item, source, packet,
        request, receipt, draft_before, checkpoint_before, draft_after,
        checkpoint_after, None, recovering,
    )
    if recovering:
        journal_bytes = resolver._read_bytes_nofollow(journal_path)
        if resolver._json_bytes(plan.journal) != journal_bytes:
            raise ValueError(
                "live authority journal does not exactly match the independently "
                "reconstructed human request"
            )
    return plan


def _validate_authority_plan(plan: _AuthorityPlan) -> None:
    if not isinstance(plan, _AuthorityPlan):
        raise TypeError("authority recovery requires a trusted authority plan")
    request = _canonical_authority_request(plan.request)
    prepare = _load_authority_prepare(plan.authority_run, plan.area)
    if prepare != plan.prepare or prepare.get("tmp") != str(plan.tmp):
        raise ValueError("authority prepare identity changed during transaction")
    prepare_path = resolver._trusted_artifact_path(
        plan.authority_run / "prepare.json", plan.authority_run
    )
    if (resolver._sha(resolver._read_bytes_nofollow(prepare_path))
            != plan.receipt.get("source_prepare_sha256")):
        raise ValueError("authority prepare bytes changed during transaction")

    source = next(
        (value for value in prepare["source"]["drafts"]
         if value["chunk"] == plan.chunk),
        None,
    )
    item = next(
        (value for value in prepare["items"] if value["fid"] == plan.fid),
        None,
    )
    if source != plan.source or item != plan.prepared_item:
        raise ValueError("authority source item changed during transaction")
    frozen_draft = resolver._trusted_artifact_path(
        plan.authority_run / source["frozen_path"], plan.authority_run
    )
    frozen_checkpoint = resolver._trusted_artifact_path(
        plan.authority_run / source["checkpoint_frozen_path"],
        plan.authority_run,
    )
    draft_before = resolver._read_bytes_nofollow(frozen_draft)
    checkpoint_before = resolver._read_bytes_nofollow(frozen_checkpoint)
    if (plan.before_bytes["draft"] != draft_before
            or plan.before_bytes["checkpoint"] != checkpoint_before):
        raise ValueError("authority before-images are not the frozen prepare sources")

    receipt_after = resolver._json_bytes(plan.receipt)
    if plan.after_bytes["receipt"] != receipt_after:
        raise ValueError("authority receipt after-image is noncanonical")
    try:
        before_rows = json.loads(draft_before)
        after_rows = json.loads(plan.after_bytes["draft"])
        checkpoint_before_value = json.loads(checkpoint_before)
        checkpoint_after_value = json.loads(plan.after_bytes["checkpoint"])
    except json.JSONDecodeError as error:
        raise ValueError("authority plan contains malformed JSON") from error
    if (not isinstance(before_rows, list) or not isinstance(after_rows, list)
            or len(before_rows) != len(after_rows)
            or not isinstance(checkpoint_before_value, dict)
            or not isinstance(checkpoint_after_value, dict)):
        raise ValueError("authority plan image schema is invalid")
    before_by_fid = {
        row.get("fid"): row for row in before_rows if isinstance(row, dict)
    }
    after_by_fid = {
        row.get("fid"): row for row in after_rows if isinstance(row, dict)
    }
    if (len(before_by_fid) != len(before_rows)
            or len(after_by_fid) != len(after_rows)
            or set(before_by_fid) != set(after_by_fid)
            or plan.fid not in after_by_fid):
        raise ValueError("authority draft row identity changed")
    for fid in before_by_fid:
        if fid != plan.fid and before_by_fid[fid] != after_by_fid[fid]:
            raise ValueError("authority plan changes an unrequested draft row")
    row = after_by_fid[plan.fid]
    packet_path = resolver._trusted_artifact_path(
        plan.authority_run / item["packet_path"], plan.authority_run
    )
    packet = json.loads(resolver._read_bytes_nofollow(packet_path))
    if packet != plan.packet:
        raise ValueError("authority frozen packet changed during transaction")
    row_errors, _ = validate_verdict_row(row, packet)
    before_row = before_by_fid[plan.fid]
    if plan.authority_kind == "human_confirmation_v2":
        reconstructed_before = copy.deepcopy(row)
        reconstructed_before.pop("human_confirmation", None)
        source_matches = reconstructed_before == before_row
    else:
        bound_override = row.get("override")
        source_matches = (
            isinstance(bound_override, dict)
            and bound_override.get("from_decision")
            == tr.decision_projection(before_row)
        )
    if row_errors or not source_matches:
        raise ValueError(
            f"authority draft after-image is invalid: {row_errors}"
        )
    receipt_errors = validate_authority_receipt(
        row, plan.tmp,
        pending={plan.receipt["receipt_sha256"]: plan.receipt},
    )
    receipt_errors.extend(_validate_receipt_source(plan.receipt, row, packet))
    if receipt_errors:
        raise ValueError(
            f"authority receipt after-image is invalid: {receipt_errors}"
        )
    bound = authority_wrapper(row)
    if (bound is None or bound[0] != plan.authority_kind
            or bound[1].get("authority_receipt_sha256")
            != plan.receipt["receipt_sha256"]
            or bound[1].get("reviewer") != request["reviewer"]
            or bound[1].get("date") != request["date"]
            or bound[1].get("note") != request["note"]
            or row.get("verdict") != request["verdict"]):
        raise ValueError("authority wrapper differs from the current human request")
    for axis, call in request["axis_calls"].items():
        value = row.get(axis)
        if (not isinstance(value, dict) or value.get("call") != call
                or value.get("evidence") != request["axis_evidence"][axis]):
            raise ValueError(f"authority effective axis {axis} differs from request")

    expected_checkpoint = copy.deepcopy(checkpoint_before_value)
    expected_checkpoint["draft_sha256"] = resolver._sha(
        plan.after_bytes["draft"]
    )
    if checkpoint_after_value != expected_checkpoint:
        raise ValueError(
            "authority checkpoint may change only the exact draft_sha256"
        )
    chunk_packets = []
    for source_row in after_rows:
        source_item = next(
            (value for value in prepare["items"]
             if value["fid"] == source_row["fid"]),
            None,
        )
        if source_item is None or source_item["chunk"] != plan.chunk:
            raise ValueError("authority chunk packet coverage changed")
        source_packet_path = resolver._trusted_artifact_path(
            plan.authority_run / source_item["packet_path"],
            plan.authority_run,
        )
        chunk_packets.append(json.loads(
            resolver._read_bytes_nofollow(source_packet_path)
        ))
    state = judge_packets.inspect_rows(
        chunk_packets, after_rows, plan.paths["draft"].name
    )
    if (state["status"] != "complete"
            or state["row_sha256"]
            != checkpoint_after_value.get("judge_row_sha256")
            or state["resolution_sha256"]
            != checkpoint_after_value.get("resolution_row_sha256")
            or state["completed"] != checkpoint_after_value.get("completed")):
        raise ValueError(
            f"authority draft/checkpoint semantics are invalid: {state['errors']}"
        )

    expected = _assemble_authority_plan(
        plan.tmp, plan.area, plan.authority_run, prepare,
        plan.source_generation, item, source, packet,
        request, plan.receipt, draft_before, checkpoint_before,
        plan.after_bytes["draft"], plan.after_bytes["checkpoint"],
        plan.before_bytes["receipt"], plan.recovering,
    )
    for field in (
        "authority_kind", "source_generation", "request", "receipt", "before_bytes",
        "after_bytes", "backup_bytes", "identity", "transaction_id",
        "paths", "journal",
    ):
        if getattr(plan, field) != getattr(expected, field):
            raise ValueError(f"authority plan {field} is noncanonical")


def _validate_authority_live_sources(plan: _AuthorityPlan) -> None:
    prepared_generation = dossier_output.validate_source_generation(
        plan.prepare["source"].get("source_generation"), plan.area
    )
    if plan.source_generation != prepared_generation:
        raise ValueError("authority source generation changed after prepare")
    root = plan.tmp
    packet_path = resolver._trusted_artifact_path(
        root / plan.prepare["source"]["packets_path"], root
    )
    if (resolver._sha(resolver._read_bytes_nofollow(packet_path))
            != plan.prepare["source"]["packets_file_sha256"]):
        raise ValueError("authority live packet source changed after prepare")
    for source in plan.prepare["source"]["drafts"]:
        if source["chunk"] == plan.chunk:
            if (resolver._trusted_artifact_path(root / source["path"], root)
                    != plan.paths["draft"]
                    or resolver._trusted_artifact_path(
                        root / source["checkpoint_path"], root
                    ) != plan.paths["checkpoint"]):
                raise ValueError("authority target paths differ from prepare")
            continue
        for path_field, hash_field in (
            ("path", "file_sha256"),
            ("checkpoint_path", "checkpoint_sha256"),
        ):
            path = resolver._trusted_artifact_path(
                root / source[path_field], root
            )
            if (resolver._sha(resolver._read_bytes_nofollow(path))
                    != source[hash_field]):
                raise ValueError(
                    f"{source[path_field]} changed after authority prepare"
                )
    publication = plan.prepare["source"]["publication"]
    for path_field, hash_field in (
        ("public_set_path", "public_set_sha256"),
    ):
        path = resolver._trusted_artifact_path(
            root / publication[path_field], root
        )
        if (resolver._sha(resolver._read_bytes_nofollow(path))
                != publication[hash_field]):
            raise ValueError(
                f"{publication[path_field]} changed after authority prepare"
            )


def _authority_archive_matches(plan: _AuthorityPlan) -> bool:
    try:
        archive = resolver._read_bytes_nofollow(plan.paths["archive"])
    except FileNotFoundError:
        return False
    expected = resolver._json_bytes(plan.journal)
    if archive != expected:
        raise ValueError("authority transaction archive differs from exact plan")
    return True


def _authority_target_states(plan: _AuthorityPlan) -> dict[str, bool]:
    states = {}
    for prefix in ("receipt", "draft", "checkpoint"):
        try:
            current = resolver._read_bytes_nofollow(plan.paths[prefix])
        except FileNotFoundError:
            current = None
        before = plan.before_bytes[prefix]
        after = plan.after_bytes[prefix]
        if current != before and current != after:
            raise ValueError(
                f"authority {prefix} matches neither trusted plan image"
            )
        states[prefix] = current == after
    if states["checkpoint"] and not states["draft"]:
        raise ValueError("authority checkpoint advanced before its draft")
    if states["draft"] and not states["receipt"]:
        receipt_only_restore = (
            states["checkpoint"] and _authority_archive_matches(plan)
        )
        if not receipt_only_restore:
            raise ValueError("authority draft advanced before its receipt")
    return states


def _authority_artifact_states(plan: _AuthorityPlan) -> dict[str, bool]:
    states = _authority_target_states(plan)
    for prefix in ("receipt", "draft", "checkpoint"):
        backup = resolver._read_bytes_nofollow(
            plan.paths[f"{prefix}_backup"]
        )
        if backup != plan.backup_bytes[prefix]:
            raise ValueError(
                f"authority {prefix} backup differs from trusted before-image"
            )
        stage = resolver._read_bytes_nofollow(plan.paths[f"{prefix}_stage"])
        if stage != plan.after_bytes[prefix]:
            raise ValueError(
                f"authority {prefix} stage differs from trusted after-image"
            )
    return states


def _finish_authority_transaction(plan: _AuthorityPlan,
                                  crash_after: str | None = None) -> None:
    _validate_authority_plan(plan)
    _validate_authority_live_sources(plan)
    expected_journal = resolver._json_bytes(plan.journal)
    try:
        journal = resolver._read_bytes_nofollow(plan.paths["journal"])
    except FileNotFoundError as error:
        raise ValueError("trusted authority plan has no live journal") from error
    if journal != expected_journal:
        raise ValueError(
            "live authority journal does not exactly match trusted plan"
        )
    states = _authority_artifact_states(plan)
    for prefix in ("receipt", "draft", "checkpoint"):
        states = _authority_artifact_states(plan)
        if states[prefix]:
            continue
        if prefix == "draft" and not states["receipt"]:
            raise ValueError("authority draft cannot precede its receipt")
        if prefix == "checkpoint" and not states["draft"]:
            raise ValueError("authority checkpoint cannot precede its draft")
        resolver._replace_verified_nofollow(
            plan.paths[f"{prefix}_stage"], plan.paths[prefix],
            resolver._sha(plan.after_bytes[prefix]),
        )
        if resolver._read_bytes_nofollow(
                plan.paths[prefix]) != plan.after_bytes[prefix]:
            raise ValueError(
                f"authority {prefix} differs from exact trusted after-image"
            )
        states[prefix] = True
        if crash_after == prefix:
            raise RuntimeError(
                f"simulated crash after authority {prefix} replacement"
            )
    for prefix in ("receipt", "draft", "checkpoint"):
        if resolver._read_bytes_nofollow(
                plan.paths[prefix]) != plan.after_bytes[prefix]:
            raise ValueError(
                f"authority {prefix} failed exact final-byte postcondition"
            )
    _validate_authority_plan(plan)
    _validate_authority_live_sources(plan)
    resolver._replace_verified_nofollow(
        plan.paths["journal"], plan.paths["archive"],
        resolver._sha(expected_journal), retire_trusted_source=True,
    )


def _commit_authority_changes(staged_drafts: dict[Path, list[dict]],
                              staged_receipts: dict[str, dict],
                              tmp: Path, slug: str, authority_run: Path,
                              prepare: dict, request: dict,
                              generation_capture,
                              crash_after: str | None = None) -> None:
    if crash_after not in (None, "journal", "receipt", "draft", "checkpoint"):
        raise ValueError("unknown authority crash cut point")
    plan = _build_authority_plan(
        staged_drafts, staged_receipts, tmp, slug, authority_run,
        prepare, request, generation_capture,
    )
    _validate_authority_plan(plan)
    _validate_authority_live_sources(plan)
    if plan.recovering:
        _finish_authority_transaction(plan, crash_after=crash_after)
        return
    states = _authority_target_states(plan)
    if all(states.values()):
        return
    for prefix in ("receipt", "draft", "checkpoint"):
        resolver._write_idempotent(
            plan.paths[f"{prefix}_backup"], plan.backup_bytes[prefix]
        )
        resolver._write_idempotent(
            plan.paths[f"{prefix}_stage"], plan.after_bytes[prefix]
        )
    _authority_artifact_states(plan)
    _validate_authority_plan(plan)
    _validate_authority_live_sources(plan)
    resolver._write_idempotent(
        plan.paths["journal"], resolver._json_bytes(plan.journal)
    )
    if crash_after == "journal":
        raise RuntimeError("simulated crash after authority journal")
    _finish_authority_transaction(plan, crash_after=crash_after)


def _authority_binding_errors(prepare: dict, prepared_item: dict, packet: dict,
                              decision: dict) -> list[str]:
    errors: list[str] = []
    try:
        packet_sha = tr.packet_sha256(packet)
    except ValueError as error:
        return [f"cannot hash packet: {error}"]
    if prepared_item.get("packet_sha256") != packet_sha:
        errors.append("source-run packet hash differs from current packet")
    primary = prepared_item.get("primary")

    def validate_plain(value: dict) -> list[str]:
        return validate_verdict_row(value, packet, allow_override=False)[0]

    errors.extend(tr.validate_envelope(
        primary, "primary", packet, validate_plain,
        expected_assignment=resolver.expected_primary_assignment(prepare, prepared_item),
        expected_packet_sha256=prepared_item.get("packet_sha256"),
        expected_version=tr.VERSION,
    ))
    primary_decision = (primary or {}).get("decision") or {}
    if tr.decision_projection(primary_decision) != tr.decision_projection(decision):
        errors.append("source-run primary differs from current decision")
    return errors


def apply_confirmations(tmp: Path, slug: str, confirmations: dict[int, str],
                        note: str, today: str, reviewer: str,
                        authority_run: Path, review_receipt_path: Path,
                        staged_drafts: dict[Path, list[dict]],
                        staged_receipts: dict[str, dict], *,
                        source_drafts: dict[Path, list[dict]] | None = None,
                        check_live_source: bool = True) -> list[str]:
    """Plan one bound same-verdict human authority without inventing a flip."""
    if len(confirmations) != 1:
        return ["  !! --confirm accepts exactly one fid per atomic invocation"]
    if not isinstance(note, str) or not note.strip():
        return ["  !! --confirm requires a non-empty --note"]
    if (not isinstance(reviewer, str)
            or tr.identity_key(reviewer) != reviewer):
        return ["  !! --confirm requires a canonical --reviewer identity"]
    try:
        prepare = _load_authority_prepare(authority_run, slug)
        review_context = resolver.load_review_receipt(
            review_receipt_path, authority_run, slug,
            expected_fid=next(iter(confirmations)),
        )
        packets = {}
        run_root = authority_run.resolve()
        for item in prepare["items"]:
            packet_path = resolver._trusted_artifact_path(
                run_root / item["packet_path"], run_root
            )
            packets[item["fid"]] = json.loads(
                resolver._read_bytes_nofollow(packet_path)
            )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return [f"  !! cannot bind confirmation authority: {error}"]
    prepared_items = {item.get("fid"): item for item in prepare["items"]}
    lines: list[str] = []
    pending = dict(confirmations)
    planner_paths = (
        sorted(source_drafts, key=str)
        if source_drafts is not None else draft_files(tmp, slug)
    )
    for raw_path in planner_paths:
        path = resolver._trusted_artifact_path(raw_path, tmp.resolve())
        if source_drafts is not None:
            if path not in source_drafts:
                return [f"  !! authority frozen source is missing {path.name}"]
            rows = copy.deepcopy(source_drafts[path])
        else:
            rows = (copy.deepcopy(staged_drafts.get(path))
                    if path in staged_drafts
                    else json.loads(resolver._read_bytes_nofollow(path)))
        changed = False
        for entry in rows:
            fid = entry.get("fid")
            if fid not in pending:
                continue
            verdict = pending.pop(fid)
            before = json.dumps(entry, sort_keys=True)
            existing_confirmation = copy.deepcopy(entry.get("human_confirmation"))
            base = dict(entry)
            base.pop("human_confirmation", None)
            packet = packets.get(fid)
            prepared_item = prepared_items.get(fid)
            if verdict not in ("KEEP", "DROP"):
                line = f"  !! #{fid}: confirmations require KEEP or DROP, got {verdict}"
            elif entry.get("override") is not None:
                line = f"  !! #{fid}: already has a human override; confirmation is redundant"
            elif entry.get("verdict") != verdict:
                line = (f"  !! #{fid}: current verdict is {entry.get('verdict')}, not {verdict}; "
                        "use receipt-bound --decide for a flip")
            elif not note.strip():
                line = f"  !! #{fid}: --confirm requires a non-empty --note"
            elif packet is None or prepared_item is None:
                line = f"  !! #{fid}: missing packet or source-run item"
            else:
                binding_errors = _authority_binding_errors(prepare, prepared_item, packet, base)
                if existing_confirmation is None and check_live_source:
                    binding_errors.extend(_authority_source_state_errors(prepare, tmp))
                if binding_errors:
                    line = f"  !! #{fid}: invalid authority binding: {binding_errors}"
                else:
                    packet_sha = tr.packet_sha256(packet)
                    decision_sha = tr.sha256_json(tr.decision_projection(base))
                    review_bindings = _review_bindings(
                        review_context, slug, authority_run, prepare,
                        prepared_item,
                    )
                    confirmation = {
                        "version": CONFIRMATION_VERSION,
                        "verdict": verdict,
                        "by": "human",
                        "reviewer": reviewer,
                        "date": today,
                        "note": note,
                        "decision_sha256": decision_sha,
                        "evidence_sha256": tr.evidence_sha256(base),
                        "packet_sha256": packet_sha,
                        "source_run_id": prepare["run_id"],
                        "review_receipt_sha256": review_bindings[
                            "review_receipt_sha256"
                        ],
                        "review_sheet_sha256": review_bindings[
                            "review_sheet_sha256"
                        ],
                        "review_item_sha256": review_bindings[
                            "review_item_sha256"
                        ],
                    }
                    entry["human_confirmation"] = confirmation
                    receipt_sha, receipt = _build_authority_receipt(
                        tmp, slug, authority_run, prepare, prepared_item, entry,
                        "human_confirmation_v2", confirmation, review_context,
                    )
                    confirmation["authority_receipt_sha256"] = receipt_sha
                    staged_receipts[receipt_sha] = receipt
                    if existing_confirmation == confirmation:
                        line = f"  #{fid}: {verdict} already confirmed by {reviewer}"
                    else:
                        line = f"  #{fid}: {verdict} confirmed by {reviewer}"
            lines.append(line + f" [{path.name}]")
            changed = changed or json.dumps(entry, sort_keys=True) != before
        if changed:
            staged_drafts[path] = rows
    for fid, verdict in pending.items():
        lines.append(f"  !! #{fid}: not in any {slug} draft, cannot confirm {verdict}")
    return lines


def apply_bound_decision(tmp: Path, slug: str, fid: int, verdict: str,
                         axis_calls: dict[str, str], axis_evidence: dict[str, str],
                         note: str, today: str, reviewer: str,
                         authority_run: Path, review_receipt_path: Path,
                         staged_drafts: dict[Path, list[dict]],
                         staged_receipts: dict[str, dict], *,
                         source_drafts: dict[Path, list[dict]] | None = None,
                         check_live_source: bool = True) -> list[str]:
    """Plan one coherent receipt-bound human flip in one canonical draft."""
    if set(axis_calls) != set(axis_evidence) or not axis_calls:
        return ["  !! --decide requires matching --axis and --axis-evidence entries"]
    if not isinstance(note, str) or not note.strip():
        return ["  !! --decide requires a non-empty --note"]
    if (not isinstance(reviewer, str)
            or tr.identity_key(reviewer) != reviewer):
        return ["  !! --decide requires a canonical --reviewer identity"]
    try:
        prepare = _load_authority_prepare(authority_run, slug)
        review_context = resolver.load_review_receipt(
            review_receipt_path, authority_run, slug, expected_fid=fid
        )
        packets = {}
        run_root = authority_run.resolve()
        for item in prepare["items"]:
            packet_path = resolver._trusted_artifact_path(
                run_root / item["packet_path"], run_root
            )
            packets[item["fid"]] = json.loads(
                resolver._read_bytes_nofollow(packet_path)
            )
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as error:
        return [f"  !! cannot bind human decision: {error}"]
    prepared_items = {item.get("fid"): item for item in prepare["items"]}
    planner_paths = (
        sorted(source_drafts, key=str)
        if source_drafts is not None else draft_files(tmp, slug)
    )
    for raw_path in planner_paths:
        path = resolver._trusted_artifact_path(raw_path, tmp.resolve())
        if source_drafts is not None:
            if path not in source_drafts:
                return [f"  !! authority frozen source is missing {path.name}"]
            rows = copy.deepcopy(source_drafts[path])
        else:
            rows = (copy.deepcopy(staged_drafts[path])
                    if path in staged_drafts
                    else json.loads(resolver._read_bytes_nofollow(path)))
        entry = next((row for row in rows if row.get("fid") == fid), None)
        if entry is None:
            continue
        if entry.get("override") is not None or entry.get("human_confirmation") is not None:
            return [f"  !! #{fid}: existing human authority is immutable in this CLI; "
                    "archive it and use a separately reviewed chained revision workflow"]
        if entry.get("verdict") == verdict:
            return [f"  !! #{fid}: verdict already {verdict}; use --confirm"]
        packet = packets.get(fid)
        prepared_item = prepared_items.get(fid)
        if packet is None or prepared_item is None:
            return [f"  !! #{fid}: missing packet or source-run item"]
        source_decision = tr.decision_projection(entry)
        binding_errors = _authority_binding_errors(
            prepare, prepared_item, packet, source_decision
        )
        if check_live_source:
            binding_errors.extend(_authority_source_state_errors(prepare, tmp))
        if binding_errors:
            return [f"  !! #{fid}: invalid authority binding: {binding_errors}"]
        packet_sha = tr.packet_sha256(packet)
        effective = copy.deepcopy(source_decision)
        effective["verdict"] = verdict
        effective["confidence"] = "strong"
        effective["resolve_hint"] = None
        for axis, call in axis_calls.items():
            if axis not in ("exists", "public", "serves") or call not in ("yes", "no", "unclear", "n/a"):
                return [f"  !! #{fid}: invalid axis update {axis}={call}"]
            evidence = axis_evidence[axis]
            if not evidence.strip():
                return [f"  !! #{fid}: empty evidence for axis {axis}"]
            effective[axis] = {"call": call, "evidence": evidence}
        row_errors, _ = validate_verdict_row(effective, packet, allow_override=False)
        if row_errors:
            return [f"  !! #{fid}: effective human decision is invalid: {row_errors}"]
        review_bindings = _review_bindings(
            review_context, slug, authority_run, prepare, prepared_item
        )
        wrapper = {
            "version": OVERRIDE_VERSION,
            "from_decision": source_decision,
            "from_decision_sha256": tr.sha256_json(source_decision),
            "from_evidence_sha256": tr.evidence_sha256(source_decision),
            "to_decision_sha256": tr.sha256_json(effective),
            "to_evidence_sha256": tr.evidence_sha256(effective),
            "by": "human",
            "reviewer": reviewer,
            "date": today,
            "note": note,
            "packet_sha256": packet_sha,
            "source_run_id": prepare["run_id"],
            "review_receipt_sha256": review_bindings[
                "review_receipt_sha256"
            ],
            "review_sheet_sha256": review_bindings[
                "review_sheet_sha256"
            ],
            "review_item_sha256": review_bindings[
                "review_item_sha256"
            ],
        }
        for field in tr.DECISION_FIELDS:
            entry.pop(field, None)
        entry.update(copy.deepcopy(effective))
        entry["override"] = wrapper
        receipt_sha, receipt = _build_authority_receipt(
            tmp, slug, authority_run, prepare, prepared_item, entry,
            "override_v3", wrapper, review_context,
        )
        wrapper["authority_receipt_sha256"] = receipt_sha
        staged_receipts[receipt_sha] = receipt
        staged_drafts[path] = rows
        return [f"  #{fid}: {source_decision.get('verdict')} -> {verdict} "
                f"(bound human decision by {reviewer}) [{path.name}]"]
    return [f"  !! #{fid}: not in any {slug} draft, cannot decide {verdict}"]


def _take_opt(argv: list[str], name: str, repeat: bool = False):
    """Pop `name VALUE` (or every occurrence when repeat) out of argv."""
    vals: list[str] = []
    while name in argv:
        i = argv.index(name)
        if i + 1 >= len(argv):
            sys.exit(f"{name} needs a value")
        vals.append(argv[i + 1])
        del argv[i:i + 2]
        if not repeat:
            break
    return vals if repeat else (vals[0] if vals else None)


def _release_locks(handles: list) -> None:
    """Unlock and close every registered handle, even if one cleanup fails."""
    first_error: BaseException | None = None
    while handles:
        handle = handles.pop()
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except BaseException as error:
            if first_error is None:
                first_error = error
        try:
            handle.close()
        except BaseException as error:
            if first_error is None:
                first_error = error
    if first_error is not None:
        raise first_error


def _direct_human_source(row: object) -> str | None:
    if not isinstance(row, dict) or not isinstance(row.get("src"), str):
        return None
    source = row["src"].strip()
    return source if source.casefold().startswith("user") else None


def _overlap_decision_signature(row: dict) -> str:
    return tr.sha256_json({
        field: copy.deepcopy(row.get(field))
        for field in tr.DECISION_FIELDS
        if field not in {"fid", "area", "osm"}
    })


def _foreign_holders(store: dict, osm_ids: list[str], slug: str) -> list[tuple[str, dict]]:
    del slug  # all holders, including same-area aliases, participate in authority checks
    component = {value for value in osm_ids if isinstance(value, str)}
    changed = True
    while changed:
        changed = False
        for store_key, held in store.items():
            if not isinstance(store_key, str) or not isinstance(held, dict):
                continue
            holder_ids = {store_key} | {
                value for value in (held.get("osm") or []) if isinstance(value, str)
            }
            if component & holder_ids and not holder_ids.issubset(component):
                component.update(holder_ids)
                changed = True
    holders = []
    seen_records = set()
    for store_key in sorted(component):
        held = store.get(store_key)
        if not isinstance(held, dict):
            continue
        identity = tr.sha256_json(held)
        if identity in seen_records:
            continue
        seen_records.add(identity)
        holders.append((store_key, held))
    return holders


def _overlap_issues(slug: str, fid: int, incoming: dict,
                    holders: list[tuple[str, dict]]) -> list[str]:
    candidates = [("incoming", incoming)] + holders
    human_candidates = [
        (label, row, _direct_human_source(row))
        for label, row in candidates if _direct_human_source(row) is not None
    ]
    issues = []
    if human_candidates:
        sources = {source.casefold() for _label, _row, source in human_candidates}
        if len(sources) != 1:
            issues.append(
                f"{slug} fid{fid}: overlapping records have different direct-human "
                "source provenance — resolve, do not discard it"
            )
        signatures = {_overlap_decision_signature(row) for _label, row in candidates}
        if len(signatures) != 1:
            issues.append(
                f"{slug} fid{fid}: direct-human and automated overlap decisions differ "
                "in effective decision content — resolve, do not discard human authority"
            )
        human_authority = {
            tr.sha256_json({
                "src": source.casefold(),
                "override": row.get("override"),
                "human_confirmation": row.get("human_confirmation"),
            })
            for _label, row, source in human_candidates
        }
        if len(human_authority) != 1:
            issues.append(
                f"{slug} fid{fid}: direct-human holders have different authority provenance"
            )
        return issues

    for osm_id, held in holders:
        if held.get("verdict") != incoming.get("verdict"):
            issues.append(
                f"{slug} fid{fid}: {incoming.get('verdict')} but {held.get('area')} "
                f"already holds {osm_id} as {held.get('verdict')} — resolve, do not overwrite"
            )
        if ((held.get("trust_resolution") or incoming.get("trust_resolution"))
                and held.get("trust_resolution") != incoming.get("trust_resolution")):
            issues.append(
                f"{slug} fid{fid}: same-verdict overlapping record at {osm_id} has "
                "different trust resolution provenance — resolve, do not discard it"
            )
        for authority_field in ("override", "human_confirmation"):
            if ((held.get(authority_field) or incoming.get(authority_field))
                    and held.get(authority_field) != incoming.get(authority_field)):
                issues.append(
                    f"{slug} fid{fid}: same-verdict overlapping record at {osm_id} has "
                    f"different {authority_field} provenance — resolve, do not discard it"
                )
    return issues


def _preferred_overlap(incoming: dict, holders: list[tuple[str, dict]]) -> dict | None:
    if not holders:
        return None
    if _direct_human_source(incoming) is not None:
        return incoming
    for _osm_id, held in holders:
        if _direct_human_source(held) is not None:
            return held
    return holders[0][1]


PUBLICATION_PROOF_REGISTRY_VERSION = 1
LEGACY_PUBLICATION_PROOF_VERSION = 1
REVIEW_PUBLICATION_PROOF_VERSION = 2
PUBLICATION_PROOF_VERSION = 3
PUBLICATION_PROOF_KIND = "parking-publication-proof"


def publication_proof_path(store_path: Path) -> Path:
    _store, proof, _floor = verdict_source.publication_artifact_filenames(
        Path(store_path).name
    )
    return Path(store_path).with_name(proof)


def publication_floor_path(store_path: Path) -> Path:
    _store, _proof, floor = verdict_source.publication_artifact_filenames(
        Path(store_path).name
    )
    return Path(store_path).with_name(floor)


def _proof_decision_row(row: dict) -> dict:
    value = copy.deepcopy(row)
    for field in (
        "src", "judged", "lat", "lon", "rings", "name", "_key", "reason",
        "publication_attestation",
    ):
        value.pop(field, None)
    return value


def _build_publication_proof(run_path: Path, prepare: dict, tmp: Path,
                             drafts: list[dict]) -> tuple[str, dict]:
    output_seal = json.loads(
        resolver._read_bytes_nofollow(run_path / "output-seal.json")
    )
    sealed_outputs = {}
    for relative, expected_hash in output_seal["outputs"].items():
        if expected_hash is None:
            sealed_outputs[relative] = None
        else:
            sealed_outputs[relative] = base64.b64encode(
                resolver._read_bytes_nofollow(
                    run_path / "sealed-inbox" / Path(relative).name
                )
            ).decode("ascii")
    chunk_receipts = {
        str(source["chunk"]): json.loads(
            resolver._read_bytes_nofollow(
                run_path / "receipts" / f"chunk-{source['chunk']:02d}.json"
            )
        )
        for source in prepare["source"]["drafts"]
    }
    packet_bindings = {}
    for item in prepare["items"]:
        packet = json.loads(
            resolver._read_bytes_nofollow(run_path / item["packet_path"])
        )
        payload, tile_hashes = tr.packet_components(packet)
        packet_bindings[str(item["fid"])] = {
            "packet": payload,
            "tile_sha256": tile_hashes,
        }
    human_authority = {}
    human_reviews = {}
    final_rows = {str(row["fid"]): _proof_decision_row(row) for row in drafts}
    for row in drafts:
        bound = authority_wrapper(row)
        if bound is None:
            continue
        receipt_sha = bound[1]["authority_receipt_sha256"]
        receipt = json.loads(
            resolver._read_bytes_nofollow(
                authority_receipt_path(tmp, prepare["area"], receipt_sha)
            )
        )
        if receipt.get("version") != AUTHORITY_RECEIPT_VERSION:
            raise ValueError(
                "legacy human authority may replay from an existing legacy proof "
                "but cannot be newly published"
            )
        source_run = Path(receipt["source_run_path"])
        review_context = resolver.load_review_receipt(
            Path(receipt["review_receipt_path"]), source_run,
            prepare["area"], expected_fid=row["fid"],
        )
        review_bindings = _review_bindings(
            review_context, prepare["area"], source_run,
            review_context["prepare"],
            next(item for item in review_context["prepare"]["items"]
                 if item["fid"] == row["fid"]),
        )
        for field, value in review_bindings.items():
            if receipt.get(field) != value:
                raise ValueError(
                    f"human authority receipt {field} differs from review evidence"
                )
        human_authority[receipt_sha] = {
            "receipt": receipt,
            "source_prepare_b64": base64.b64encode(
                review_context["prepare_bytes"]
            ).decode("ascii"),
        }
        review_sha = receipt["review_receipt_sha256"]
        review_bundle = {
            "receipt_b64": base64.b64encode(
                review_context["receipt_bytes"]
            ).decode("ascii"),
            "sheet_b64": base64.b64encode(
                review_context["sheet_bytes"]
            ).decode("ascii"),
            "source_prepare_b64": base64.b64encode(
                review_context["prepare_bytes"]
            ).decode("ascii"),
            "source_artifacts_b64": {
                relative: base64.b64encode(raw).decode("ascii")
                for relative, raw in sorted(
                    review_context["source_artifacts"].items()
                )
            },
        }
        existing_review = human_reviews.get(review_sha)
        if existing_review is not None and existing_review != review_bundle:
            raise ValueError("review receipt deduplication found different exact bytes")
        human_reviews[review_sha] = review_bundle
    rendered_prompts = {
        assignment["prompt_path"]: base64.b64encode(
            resolver._read_bytes_nofollow(run_path / assignment["prompt_path"])
        ).decode("ascii")
        for item in prepare["items"]
        for assignment in item["assignments"].values()
    }
    prompt_templates = {
        role: base64.b64encode(
            resolver._read_bytes_nofollow(run_path / "templates" / f"{role}.md")
        ).decode("ascii")
        for role in prepare["prompt_template_sha256"]
    }
    normative_documents = {
        name: base64.b64encode(
            resolver._read_bytes_nofollow(run_path / "rules" / name)
        ).decode("ascii")
        for name in prepare["normative_document_sha256"]
    }
    terminal_drafts = {}
    terminal_checkpoints = {}
    for source in prepare["source"]["drafts"]:
        chunk_key = str(source["chunk"])
        terminal_drafts[chunk_key] = base64.b64encode(
            resolver._read_bytes_nofollow(tmp / source["path"])
        ).decode("ascii")
        terminal_checkpoints[chunk_key] = base64.b64encode(
            resolver._read_bytes_nofollow(tmp / source["checkpoint_path"])
        ).decode("ascii")
    body = {
        "version": PUBLICATION_PROOF_VERSION,
        "kind": PUBLICATION_PROOF_KIND,
        "run_id": prepare["run_id"],
        "prepare": copy.deepcopy(prepare),
        "output_seal": output_seal,
        "sealed_outputs": sealed_outputs,
        "rendered_prompts": rendered_prompts,
        "prompt_templates": prompt_templates,
        "normative_documents": normative_documents,
        "chunk_receipts": chunk_receipts,
        "terminal_drafts": terminal_drafts,
        "terminal_checkpoints": terminal_checkpoints,
        "packet_bindings": packet_bindings,
        "final_rows": final_rows,
        "human_authority": human_authority,
        "human_reviews": human_reviews,
    }
    proof_sha = tr.sha256_json(body)
    return proof_sha, {**body, "proof_sha256": proof_sha}


def _load_publication_proofs(path: Path) -> dict:
    try:
        raw = resolver._read_bytes_nofollow(path)
    except FileNotFoundError:
        return {"version": PUBLICATION_PROOF_REGISTRY_VERSION, "proofs": {}}
    value = verdict_source._strict_json_loads(raw, "publication proof registry")
    if (not isinstance(value, dict) or set(value) != {"version", "proofs"}
            or type(value.get("version")) is not int
            or value["version"] != PUBLICATION_PROOF_REGISTRY_VERSION
            or not isinstance(value.get("proofs"), dict)):
        raise ValueError("publication proof registry is malformed")
    for proof_sha, proof in value["proofs"].items():
        if not isinstance(proof_sha, str) or not isinstance(proof, dict):
            raise ValueError("publication proof registry entry is malformed")
        body = dict(proof)
        claimed = body.pop("proof_sha256", None)
        if claimed != proof_sha or tr.sha256_json(body) != proof_sha:
            raise ValueError("publication proof registry entry hash mismatch")
    return value


PUBLICATION_TRANSACTION_VERSION = 4
PUBLICATION_TRANSACTION_KIND = "parking-publication-transaction"
PUBLICATION_AUTHORIZATION_VERSION = 2
PUBLICATION_AUTHORIZATION_KIND = "parking-publication-authorization"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _canonical_publication_authorization(context: dict) -> dict:
    required = {
        "version", "kind", "area", "resolver_run_path", "resolver_run_id",
        "resolver_prepare_sha256", "output_seal_sha256",
        "terminal_receipts", "publication_fids", "publication_proof_sha256",
        "human_review_receipts",
    }
    if not isinstance(context, dict) or set(context) != required:
        raise ValueError("publication authorization context is malformed")
    if (type(context.get("version")) is not int
            or context["version"] != PUBLICATION_AUTHORIZATION_VERSION
            or context.get("kind") != PUBLICATION_AUTHORIZATION_KIND
            or tr.area_slug(context.get("area")) != context.get("area")):
        raise ValueError("publication authorization identity is malformed")
    run_path = context.get("resolver_run_path")
    if (not isinstance(run_path, str) or not Path(run_path).is_absolute()
            or str(Path(run_path).resolve()) != run_path):
        raise ValueError("publication authorization run path is noncanonical")
    for field in (
        "resolver_run_id", "resolver_prepare_sha256", "output_seal_sha256",
        "publication_proof_sha256",
    ):
        if (not isinstance(context.get(field), str)
                or _SHA256_RE.fullmatch(context[field]) is None):
            raise ValueError(f"publication authorization {field} is invalid")
    receipts = context.get("terminal_receipts")
    if not isinstance(receipts, list) or not receipts:
        raise ValueError("publication authorization receipt vector is empty")
    expected_receipt_keys = {
        "chunk", "kind", "transaction_id", "receipt_sha256",
    }
    for receipt in receipts:
        if (not isinstance(receipt, dict) or set(receipt) != expected_receipt_keys
                or type(receipt.get("chunk")) is not int
                or receipt["chunk"] < 0
                or receipt.get("kind") not in (
                    "parking-trust-receipt",
                    "parking-trust-preservation-receipt",
                )):
            raise ValueError("publication authorization receipt vector is malformed")
        for field in ("transaction_id", "receipt_sha256"):
            if (not isinstance(receipt.get(field), str)
                    or _SHA256_RE.fullmatch(receipt[field]) is None):
                raise ValueError(
                    f"publication authorization receipt {field} is invalid"
                )
    if receipts != sorted(receipts, key=lambda value: value["chunk"]):
        raise ValueError("publication authorization receipt vector is noncanonical")
    if len({receipt["chunk"] for receipt in receipts}) != len(receipts):
        raise ValueError("publication authorization receipt chunks are duplicated")
    fids = context.get("publication_fids")
    if (not isinstance(fids, list) or not fids
            or any(type(fid) is not int or fid < 0 for fid in fids)
            or fids != sorted(set(fids))):
        raise ValueError("publication authorization fid vector is noncanonical")
    review_receipts = context.get("human_review_receipts")
    if (not isinstance(review_receipts, list)
            or review_receipts != sorted(set(review_receipts))
            or any(not isinstance(value, str)
                   or _SHA256_RE.fullmatch(value) is None
                   for value in review_receipts)):
        raise ValueError(
            "publication authorization human review receipt vector is noncanonical"
        )
    return copy.deepcopy(context)


def _publication_authorization_context(run_path: Path, prepare: dict,
                                       proof_sha256: str, proof: dict) -> dict:
    proof_body = copy.deepcopy(proof)
    claimed_proof_sha = proof_body.pop("proof_sha256", None)
    if (claimed_proof_sha != proof_sha256
            or tr.sha256_json(proof_body) != proof_sha256):
        raise ValueError("publication proof does not match authorization context")
    if (proof.get("version") != PUBLICATION_PROOF_VERSION
            or proof.get("run_id") != prepare.get("run_id")
            or proof.get("prepare") != prepare):
        raise ValueError("publication proof resolver identity mismatch")
    output_seal = proof.get("output_seal")
    if not isinstance(output_seal, dict):
        raise ValueError("publication proof has no output seal")
    seal_body = dict(output_seal)
    seal_sha256 = seal_body.pop("seal_sha256", None)
    if (not isinstance(seal_sha256, str)
            or seal_sha256 != tr.sha256_json(seal_body)
            or output_seal.get("run_id") != prepare.get("run_id")):
        raise ValueError("publication proof output seal identity mismatch")
    chunk_receipts = proof.get("chunk_receipts")
    if not isinstance(chunk_receipts, dict):
        raise ValueError("publication proof terminal receipts are malformed")
    receipt_vector = []
    for raw_chunk, receipt in sorted(
            chunk_receipts.items(), key=lambda item: int(item[0])):
        if (not isinstance(receipt, dict)
                or receipt.get("chunk") != int(raw_chunk)
                or receipt.get("run_id") != prepare.get("run_id")
                or receipt.get("output_seal_sha256") != seal_sha256):
            raise ValueError("publication proof terminal receipt identity mismatch")
        receipt_vector.append({
            "chunk": int(raw_chunk),
            "kind": receipt.get("kind"),
            "transaction_id": receipt.get("transaction_id"),
            "receipt_sha256": tr.sha256_json(receipt),
        })
    final_rows = proof.get("final_rows")
    if not isinstance(final_rows, dict):
        raise ValueError("publication proof final rows are malformed")
    context = {
        "version": PUBLICATION_AUTHORIZATION_VERSION,
        "kind": PUBLICATION_AUTHORIZATION_KIND,
        "area": prepare.get("area"),
        "resolver_run_path": str(run_path.resolve()),
        "resolver_run_id": prepare.get("run_id"),
        "resolver_prepare_sha256": tr.sha256_json(prepare),
        "output_seal_sha256": seal_sha256,
        "terminal_receipts": receipt_vector,
        "publication_fids": sorted(int(fid) for fid in final_rows),
        "publication_proof_sha256": proof_sha256,
        "human_review_receipts": sorted(
            (proof.get("human_reviews") or {}).keys()
        ),
    }
    return _canonical_publication_authorization(context)


def _publication_candidate_path(data_root: Path, root_filename: str,
                                parent_sha256: str,
                                candidate_sha256: str) -> Path:
    verdict_source.publication_artifact_filenames(root_filename)
    for value in (parent_sha256, candidate_sha256):
        if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
            raise ValueError("publication candidate lineage hash is malformed")
    root = Path(data_root).resolve()
    path = (
        root / ".trekdex-publication-transactions" / "candidates"
        / parent_sha256 / f"{candidate_sha256}.json"
    )
    return resolver._trusted_artifact_path(path, root)


def _publication_transaction_paths(
        store_path: Path, proof_path: Path, artifact_id: str,
        archive_id: str | None = None,
        *, floor_path: Path | None = None,
        candidate_path: Path | None = None) -> dict[str, Path]:
    root = store_path.parent.resolve()
    floor_path = floor_path or publication_floor_path(store_path)
    tx_root = root / ".trekdex-publication-transactions"
    archive_id = archive_id or artifact_id
    paths = {
        "store": store_path.absolute(),
        "proof": proof_path.absolute(),
        "floor": floor_path.absolute(),
        "store_stage": tx_root / "staged" / f"{store_path.name}.{artifact_id}.json",
        "proof_stage": tx_root / "staged" / f"{proof_path.name}.{artifact_id}.json",
        "floor_stage": tx_root / "staged" / f"{floor_path.name}.{artifact_id}.json",
        "store_backup": tx_root / "backups" / f"{store_path.name}.{artifact_id}.json",
        "proof_backup": tx_root / "backups" / f"{proof_path.name}.{artifact_id}.json",
        "floor_backup": tx_root / "backups" / f"{floor_path.name}.{artifact_id}.json",
        "journal": tx_root / f"{store_path.name}.journal.json",
        "archive": tx_root / "Archive" / f"{store_path.name}.{archive_id}.json",
    }
    if candidate_path is not None:
        candidate = resolver._trusted_artifact_path(candidate_path, root)
        paths["candidate"] = candidate
        paths["candidate_stage"] = (
            tx_root / "staged" / f"{candidate.name}.{artifact_id}.json"
        )
    return {
        key: resolver._trusted_artifact_path(path, root)
        for key, path in paths.items()
    }


def _read_optional_bytes(path: Path) -> bytes | None:
    try:
        return resolver._read_bytes_nofollow(path)
    except FileNotFoundError:
        return None


@dataclass(frozen=True)
class _PublicationPlan:
    store_path: Path
    proof_path: Path
    floor_path: Path
    before_bytes: dict[str, bytes | None]
    after_bytes: dict[str, bytes]
    authorization: dict
    plan_id: str
    transaction_id: str
    paths: dict[str, Path]
    journal: dict
    recovering: bool
    spec: object
    baseline: object
    root_path: Path | None = None
    parent_root: object | None = None
    candidate_root: object | None = None
    candidate_bytes: bytes | None = None


def _publication_root_identity(root_path: Path | None, parent_root,
                               candidate_path: Path | None,
                               candidate_bytes: bytes | None):
    if root_path is None:
        if any(value is not None for value in (
                parent_root, candidate_path, candidate_bytes)):
            raise ValueError("partial publication trust root transition")
        return None
    if (not isinstance(parent_root, verdict_source.PublicationTrustRoot)
            or candidate_path is None or candidate_bytes is None):
        raise ValueError("publication trust root transition is incomplete")
    return {
        "trusted_root_path": str(Path(root_path).resolve()),
        "parent_root_sha256": parent_root.exact_sha256,
        "candidate_root_path": str(Path(candidate_path).resolve()),
        "candidate_root_sha256": resolver._sha(candidate_bytes),
    }


def _build_publication_plan(
        store_path: Path, store: dict, proof_path: Path, proof_registry: dict,
        authorization_context: dict, publication_configuration=None
) -> _PublicationPlan:
    if not isinstance(store, dict) or not isinstance(proof_registry, dict):
        raise ValueError("publication plan store/proof inputs must be objects")
    authorization = _canonical_publication_authorization(
        authorization_context
    )
    floor_path = publication_floor_path(store_path)

    root_path = None
    parent_root = None
    candidate_root = None
    candidate_bytes = None
    candidate_path = None
    if publication_configuration is None:
        spec, baseline = resolver.replay_trust.publication_store_policy(
            store_path.name
        )
    else:
        (specs, root_path, _baseline_path, _root_bytes,
         parent_root) = resolver.replay_trust.load_pinned_publication_root(
            publication_configuration
        )
        spec = next(
            (value for value in specs if value.filename == store_path.name),
            None,
        )
        if spec is None:
            raise ValueError("publication target is absent from the pinned root")
        baseline = None

    after_bytes = {
        "store": json.dumps(
            store, indent=0, ensure_ascii=False, allow_nan=False
        ).encode("utf-8"),
        "proof": resolver._json_bytes(proof_registry),
    }
    fixed_journal = _publication_transaction_paths(
        store_path, proof_path, "fixed", floor_path=floor_path
    )["journal"]
    recovering = os.path.lexists(fixed_journal)
    hinted_plan_id = None
    before_bytes = {}
    corpus = None

    if parent_root is not None:
        hinted_paths = None
        if recovering:
            journal_hint = verdict_source._strict_json_loads(
                resolver._read_bytes_nofollow(fixed_journal),
                "live publication journal",
            )
            hinted_plan_id = (
                journal_hint.get("plan_id")
                if isinstance(journal_hint, dict) else None
            )
            if (not isinstance(hinted_plan_id, str)
                    or _SHA256_RE.fullmatch(hinted_plan_id) is None):
                raise ValueError("live publication journal has no canonical plan id")
            hinted_paths = _publication_transaction_paths(
                store_path, proof_path, hinted_plan_id, floor_path=floor_path
            )
        rooted_entry = parent_root.stores_by_name[store_path.name]
        expected_hashes = {
            "store": rooted_entry["store_sha256"],
            "proof": rooted_entry["proof_registry_sha256"],
            "floor": rooted_entry["publication_floor_sha256"],
        }
        for prefix in ("store", "proof", "floor"):
            current = _read_optional_bytes(
                _publication_transaction_paths(
                    store_path, proof_path, "fixed", floor_path=floor_path
                )[prefix]
            )
            expected_hash = expected_hashes[prefix]
            current_is_rooted = (
                current is None if expected_hash is None
                else current is not None and resolver._sha(current) == expected_hash
            )
            if current_is_rooted or not recovering:
                before_bytes[prefix] = current
            else:
                before_bytes[prefix] = _read_optional_bytes(
                    hinted_paths[f"{prefix}_backup"]
                )
        if before_bytes["floor"] is None:
            raise ValueError("publication floor before-image is missing")
        corpus = resolver.replay_trust.validate_locked_publication_corpus(
            store_path.parent, publication_configuration, resolver,
            root=parent_root, target_store=store_path.name,
            target_before_bytes=before_bytes,
            allow_target_journal=recovering,
        )
        baseline = corpus["baseline"]
        after_bytes["floor"] = (
            resolver.replay_trust.build_publication_floor_after_bytes(
                spec, before_bytes["floor"], after_bytes["store"]
            )
        )
    else:
        current_floor_bytes = _read_optional_bytes(floor_path)
        if current_floor_bytes is None:
            raise ValueError(
                f"{store_path.name} publication floor is required before publication"
            )
        after_bytes["floor"] = (
            resolver.replay_trust.build_publication_floor_after_bytes(
                spec, current_floor_bytes, after_bytes["store"]
            )
        )

    if parent_root is not None:
        candidate_document = (
            verdict_source.build_publication_trust_root_successor(
                parent_root, store_path.name, after_bytes["store"],
                after_bytes["proof"], after_bytes["floor"],
            )
        )
        candidate_bytes = verdict_source.publication_trust_root_json_bytes(
            candidate_document
        )
        candidate_sha256 = resolver._sha(candidate_bytes)
        candidate_path = _publication_candidate_path(
            store_path.parent, root_path.name, parent_root.exact_sha256,
            candidate_sha256,
        )
        existing_candidate = _read_optional_bytes(candidate_path)
        if (existing_candidate is not None
                and existing_candidate != candidate_bytes):
            raise ValueError(
                "publication candidate root path has different existing bytes"
            )

    fixed_paths = _publication_transaction_paths(
        store_path, proof_path, "fixed", floor_path=floor_path,
        candidate_path=candidate_path,
    )
    targets = {
        prefix: str(fixed_paths[prefix])
        for prefix in ("store", "proof", "floor")
    }
    after_hashes = {
        prefix: resolver._sha(value) for prefix, value in after_bytes.items()
    }
    root_identity = _publication_root_identity(
        root_path, parent_root, candidate_path, candidate_bytes
    )
    plan_id = tr.sha256_json({
        "version": PUBLICATION_TRANSACTION_VERSION,
        "kind": "parking-publication-plan",
        "targets": targets,
        "after_sha256": after_hashes,
        "publication_trust_root": root_identity,
        "authorization": authorization,
    })
    candidate_paths = _publication_transaction_paths(
        store_path, proof_path, plan_id, floor_path=floor_path,
        candidate_path=candidate_path,
    )
    if parent_root is not None:
        if recovering and hinted_plan_id != plan_id:
            raise ValueError(
                "live publication journal cannot select a different rooted plan"
            )
    else:
        for prefix in ("store", "proof", "floor"):
            current = _read_optional_bytes(candidate_paths[prefix])
            if recovering and current == after_bytes[prefix]:
                before_bytes[prefix] = _read_optional_bytes(
                    candidate_paths[f"{prefix}_backup"]
                )
            else:
                before_bytes[prefix] = current
        if before_bytes["floor"] is None:
            raise ValueError("publication floor before-image is missing")

    if parent_root is not None:
        candidate_root = verdict_source.parse_publication_trust_root(
            candidate_bytes, corpus["specs"], resolver._sha(candidate_bytes),
            corpus["baseline_path"].name,
        )
        if (candidate_root.generation != parent_root.generation + 1
                or candidate_root.parent_root_sha256
                != parent_root.exact_sha256):
            raise ValueError("publication candidate root lineage mismatch")
        candidate_stores = dict(corpus["store_bytes"])
        candidate_proofs = dict(corpus["proof_bytes"])
        candidate_floors = dict(corpus["floor_bytes"])
        candidate_stores[store_path.name] = after_bytes["store"]
        candidate_proofs[store_path.name] = after_bytes["proof"]
        candidate_floors[store_path.name] = after_bytes["floor"]
        verdict_source.validate_publication_trust_root_image(
            candidate_root, corpus["specs"], candidate_stores,
            candidate_proofs, candidate_floors,
            corpus["baseline_path"].name, corpus["baseline_bytes"],
            corpus["dossier_bytes"],
        )

    before_hashes = {
        prefix: (resolver._sha(value) if value is not None else None)
        for prefix, value in before_bytes.items()
    }
    transaction_identity = {
        "version": PUBLICATION_TRANSACTION_VERSION,
        "kind": "parking-publication-transaction-identity",
        "targets": targets,
        "before_sha256": before_hashes,
        "after_sha256": after_hashes,
        "publication_trust_root": root_identity,
        "authorization": authorization,
    }
    transaction_id = tr.sha256_json(transaction_identity)
    paths = _publication_transaction_paths(
        store_path, proof_path, plan_id, archive_id=transaction_id,
        floor_path=floor_path, candidate_path=candidate_path,
    )
    journal = {
        "version": PUBLICATION_TRANSACTION_VERSION,
        "kind": PUBLICATION_TRANSACTION_KIND,
        "plan_id": plan_id,
        "transaction_id": transaction_id,
        "store_before_sha256": before_hashes["store"],
        "store_after_sha256": after_hashes["store"],
        "proof_before_sha256": before_hashes["proof"],
        "proof_after_sha256": after_hashes["proof"],
        "floor_before_sha256": before_hashes["floor"],
        "floor_after_sha256": after_hashes["floor"],
        "publication_trust_root": root_identity,
        "authorization": authorization,
        "paths": {key: str(value) for key, value in paths.items()},
    }
    return _PublicationPlan(
        store_path=paths["store"],
        proof_path=paths["proof"],
        floor_path=paths["floor"],
        before_bytes=before_bytes,
        after_bytes=after_bytes,
        authorization=authorization,
        plan_id=plan_id,
        transaction_id=transaction_id,
        paths=paths,
        journal=journal,
        recovering=recovering,
        spec=spec,
        baseline=baseline,
        root_path=root_path,
        parent_root=parent_root,
        candidate_root=candidate_root,
        candidate_bytes=candidate_bytes,
    )


_BASELINE_UNSET = object()


def _validate_publication_after_images(
        store_bytes: bytes, proof_bytes: bytes, floor_bytes: bytes,
        store_name: str = "publication-store.json",
        *, spec=None, baseline=_BASELINE_UNSET) -> None:
    store = json.loads(store_bytes)
    proof_registry = json.loads(proof_bytes)
    if not isinstance(store, dict) or not isinstance(proof_registry, dict):
        raise ValueError("publication after-images must be JSON objects")
    if spec is None or baseline is _BASELINE_UNSET:
        resolved_spec, resolved_baseline = (
            resolver.replay_trust.publication_store_policy(store_name)
        )
        if spec is None:
            spec = resolved_spec
        if baseline is _BASELINE_UNSET:
            baseline = resolved_baseline
    resolver.replay_trust.validate_store_proof_image(
        spec, store, proof_registry, baseline
    )
    floor = verdict_source.parse_publication_floor(floor_bytes, spec)
    resolver.replay_trust.validate_store_floor_image(spec, store, floor)


def _validate_publication_plan(plan: _PublicationPlan) -> None:
    if not isinstance(plan, _PublicationPlan):
        raise TypeError("publication recovery requires a trusted publication plan")
    authorization = _canonical_publication_authorization(plan.authorization)
    if authorization != plan.authorization:
        raise ValueError("publication plan authorization is noncanonical")
    prefixes = ("store", "proof", "floor")
    if (set(plan.before_bytes) != set(prefixes)
            or set(plan.after_bytes) != set(prefixes)):
        raise ValueError("publication plan image set is noncanonical")

    candidate_path = plan.paths.get("candidate")
    root_identity = _publication_root_identity(
        plan.root_path, plan.parent_root, candidate_path,
        plan.candidate_bytes,
    )
    if root_identity is None:
        if plan.candidate_root is not None:
            raise ValueError("unrooted publication plan has a candidate root")
    else:
        if (not isinstance(plan.candidate_root,
                           verdict_source.PublicationTrustRoot)
                or plan.candidate_root.exact_sha256
                != root_identity["candidate_root_sha256"]
                or plan.candidate_root.parent_root_sha256
                != plan.parent_root.exact_sha256
                or plan.candidate_root.generation
                != plan.parent_root.generation + 1):
            raise ValueError("publication candidate root identity mismatch")
        expected_candidate = verdict_source.publication_trust_root_json_bytes(
            verdict_source.build_publication_trust_root_successor(
                plan.parent_root, plan.store_path.name,
                plan.after_bytes["store"], plan.after_bytes["proof"],
                plan.after_bytes["floor"],
            )
        )
        if expected_candidate != plan.candidate_bytes:
            raise ValueError("publication candidate root is not the exact successor")

    fixed_paths = _publication_transaction_paths(
        plan.store_path, plan.proof_path, "fixed", floor_path=plan.floor_path,
        candidate_path=candidate_path,
    )
    targets = {
        prefix: str(fixed_paths[prefix]) for prefix in prefixes
    }
    after_hashes = {
        prefix: resolver._sha(plan.after_bytes[prefix])
        for prefix in prefixes
    }
    expected_plan_id = tr.sha256_json({
        "version": PUBLICATION_TRANSACTION_VERSION,
        "kind": "parking-publication-plan",
        "targets": targets,
        "after_sha256": after_hashes,
        "publication_trust_root": root_identity,
        "authorization": authorization,
    })
    if plan.plan_id != expected_plan_id:
        raise ValueError("publication plan identity mismatch")
    before_hashes = {
        prefix: (
            resolver._sha(plan.before_bytes[prefix])
            if plan.before_bytes[prefix] is not None else None
        )
        for prefix in prefixes
    }
    if plan.before_bytes["floor"] is None:
        raise ValueError("publication floor before-image is missing")
    expected_transaction_id = tr.sha256_json({
        "version": PUBLICATION_TRANSACTION_VERSION,
        "kind": "parking-publication-transaction-identity",
        "targets": targets,
        "before_sha256": before_hashes,
        "after_sha256": after_hashes,
        "publication_trust_root": root_identity,
        "authorization": authorization,
    })
    if plan.transaction_id != expected_transaction_id:
        raise ValueError("publication transaction identity mismatch")
    expected_paths = _publication_transaction_paths(
        plan.store_path, plan.proof_path, plan.plan_id,
        archive_id=plan.transaction_id, floor_path=plan.floor_path,
        candidate_path=candidate_path,
    )
    if plan.paths != expected_paths:
        raise ValueError("publication plan paths are noncanonical")
    expected_journal = {
        "version": PUBLICATION_TRANSACTION_VERSION,
        "kind": PUBLICATION_TRANSACTION_KIND,
        "plan_id": plan.plan_id,
        "transaction_id": plan.transaction_id,
        "store_before_sha256": before_hashes["store"],
        "store_after_sha256": after_hashes["store"],
        "proof_before_sha256": before_hashes["proof"],
        "proof_after_sha256": after_hashes["proof"],
        "floor_before_sha256": before_hashes["floor"],
        "floor_after_sha256": after_hashes["floor"],
        "publication_trust_root": root_identity,
        "authorization": authorization,
        "paths": {key: str(value) for key, value in expected_paths.items()},
    }
    if plan.journal != expected_journal:
        raise ValueError("publication plan journal is noncanonical")
    if (not isinstance(plan.spec, verdict_source.StoreSpec)
            or plan.spec.filename != plan.store_path.name):
        raise ValueError("publication plan store policy is malformed")
    resolver.replay_trust.validate_monotonic_publication_transition(
        plan.store_path.name,
        plan.before_bytes["store"],
        plan.after_bytes["store"],
    )
    resolver.replay_trust.validate_publication_floor_transition(
        plan.spec, plan.before_bytes["floor"], plan.after_bytes["floor"]
    )
    expected_floor = resolver.replay_trust.build_publication_floor_after_bytes(
        plan.spec, plan.before_bytes["floor"], plan.after_bytes["store"]
    )
    if expected_floor != plan.after_bytes["floor"]:
        raise ValueError("publication floor after-image is not the exact union")
    _validate_publication_after_images(
        plan.after_bytes["store"], plan.after_bytes["proof"],
        plan.after_bytes["floor"], plan.store_path.name,
        spec=plan.spec, baseline=plan.baseline,
    )


def _publication_archive_matches(plan: _PublicationPlan) -> bool:
    try:
        archive = resolver._read_bytes_nofollow(plan.paths["archive"])
    except FileNotFoundError:
        return False
    expected = resolver._json_bytes(plan.journal)
    if archive != expected:
        raise ValueError(
            "publication transaction archive differs from exact trusted plan"
        )
    return True


def _publication_artifact_states(plan: _PublicationPlan) -> dict[str, bool]:
    states = {}
    for prefix in ("proof", "floor", "store"):
        current = _read_optional_bytes(plan.paths[prefix])
        before = plan.before_bytes[prefix]
        after = plan.after_bytes[prefix]
        if current != before and current != after:
            raise ValueError(
                f"publication {prefix} matches neither trusted plan image"
            )
        backup = _read_optional_bytes(plan.paths[f"{prefix}_backup"])
        if backup != before:
            raise ValueError(
                f"publication {prefix} backup differs from trusted before-image"
            )
        stage = _read_optional_bytes(plan.paths[f"{prefix}_stage"])
        if stage is not None and stage != after:
            raise ValueError(
                f"publication {prefix} stage differs from trusted after-image"
            )
        is_after = current == after
        if not is_after and stage != after:
            raise ValueError(
                f"publication {prefix} stage is missing trusted after-image"
            )
        states[prefix] = is_after
    floor_advanced = (
        plan.before_bytes["floor"] != plan.after_bytes["floor"]
        and states["floor"]
    )
    if floor_advanced and not states["proof"]:
        raise ValueError("publication floor advanced before its proof")
    if states["store"] and not (states["proof"] and states["floor"]):
        raise ValueError("publication store advanced before its proof and floor")

    if plan.candidate_bytes is not None:
        current = _read_optional_bytes(plan.paths["candidate"])
        if current is not None and current != plan.candidate_bytes:
            raise ValueError(
                "publication candidate root path has different existing bytes"
            )
        stage = _read_optional_bytes(plan.paths["candidate_stage"])
        if stage is not None and stage != plan.candidate_bytes:
            raise ValueError(
                "publication candidate root stage differs from trusted plan"
            )
        candidate_after = current == plan.candidate_bytes
        if not candidate_after and stage != plan.candidate_bytes:
            raise ValueError(
                "publication candidate root stage is missing trusted bytes"
            )
        if (plan.recovering and candidate_after
                and not all(states[prefix]
                            for prefix in ("proof", "floor", "store"))):
            raise ValueError(
                "publication candidate root advanced before proof, floor, and store"
            )
        states["candidate"] = candidate_after
    if _publication_archive_matches(plan):
        if not plan.recovering or not all(states.values()):
            raise ValueError(
                "publication archive exists before exact target completion"
            )
    return states


def _finish_publication_transaction(
        plan: _PublicationPlan, crash_after_proof: bool = False,
        crash_after_floor: bool = False,
        crash_after_candidate: bool = False) -> None:
    _validate_publication_plan(plan)
    expected_journal_bytes = resolver._json_bytes(plan.journal)
    try:
        journal_bytes = resolver._read_bytes_nofollow(plan.paths["journal"])
    except FileNotFoundError as error:
        raise ValueError("trusted publication plan has no live journal") from error
    if journal_bytes != expected_journal_bytes:
        raise ValueError(
            "live publication journal does not exactly match trusted publication plan"
        )
    states = _publication_artifact_states(plan)
    for prefix in ("proof", "floor", "store"):
        if states[prefix]:
            continue
        resolver._replace_verified_nofollow(
            plan.paths[f"{prefix}_stage"], plan.paths[prefix],
            resolver._sha(plan.after_bytes[prefix]),
        )
        if _read_optional_bytes(plan.paths[prefix]) != plan.after_bytes[prefix]:
            raise ValueError(
                f"publication {prefix} differs from exact trusted after-image"
            )
        if prefix == "proof" and crash_after_proof:
            raise RuntimeError("simulated crash after publication proof replacement")
        if prefix == "floor" and crash_after_floor:
            raise RuntimeError("simulated crash after publication floor replacement")
    for prefix in ("proof", "floor", "store"):
        if _read_optional_bytes(plan.paths[prefix]) != plan.after_bytes[prefix]:
            raise ValueError(
                f"publication {prefix} failed exact final-byte postcondition"
            )

    # The non-authoritative successor is installed only after the canonical
    # proof → floor → store postcondition. The trusted root and code pin are
    # intentionally untouched.
    if plan.candidate_bytes is not None:
        if not states.get("candidate", False):
            resolver._replace_verified_nofollow(
                plan.paths["candidate_stage"], plan.paths["candidate"],
                resolver._sha(plan.candidate_bytes),
            )
        if _read_optional_bytes(plan.paths["candidate"]) != plan.candidate_bytes:
            raise ValueError(
                "publication candidate root failed exact final-byte postcondition"
            )
        if crash_after_candidate:
            raise RuntimeError("simulated crash after publication candidate root")

    resolver._replace_verified_nofollow(
        plan.paths["journal"], plan.paths["archive"],
        resolver._sha(expected_journal_bytes),
        retire_trusted_source=True,
    )


def _commit_publication(
        store_path: Path, store: dict, proof_path: Path,
        proof_registry: dict, authorization_context: dict,
        crash_after_proof: bool = False, crash_after_floor: bool = False,
        crash_after_candidate: bool = False,
        publication_configuration=None) -> None:
    plan = _build_publication_plan(
        store_path, store, proof_path, proof_registry, authorization_context,
        publication_configuration=publication_configuration,
    )
    _validate_publication_plan(plan)
    if plan.recovering:
        _finish_publication_transaction(
            plan, crash_after_proof=crash_after_proof,
            crash_after_floor=crash_after_floor,
            crash_after_candidate=crash_after_candidate,
        )
        return
    canonical_complete = all(
        _read_optional_bytes(plan.paths[prefix]) == plan.after_bytes[prefix]
        for prefix in ("store", "proof", "floor")
    )
    candidate_complete = (
        plan.candidate_bytes is None
        or _read_optional_bytes(plan.paths["candidate"])
        == plan.candidate_bytes
    )
    if canonical_complete and candidate_complete:
        return
    for prefix in ("store", "proof", "floor"):
        before = plan.before_bytes[prefix]
        backup = plan.paths[f"{prefix}_backup"]
        if before is None:
            if _read_optional_bytes(backup) is not None:
                raise ValueError(
                    f"publication {prefix} has an unexpected stale backup"
                )
        else:
            resolver._write_idempotent(backup, before)
        resolver._write_idempotent(
            plan.paths[f"{prefix}_stage"], plan.after_bytes[prefix]
        )
    if plan.candidate_bytes is not None:
        resolver._write_idempotent(
            plan.paths["candidate_stage"], plan.candidate_bytes
        )
    _publication_artifact_states(plan)
    resolver._write_idempotent(
        plan.paths["journal"], resolver._json_bytes(plan.journal)
    )
    _finish_publication_transaction(
        plan, crash_after_proof=crash_after_proof,
        crash_after_floor=crash_after_floor,
        crash_after_candidate=crash_after_candidate,
    )


def _publication_attestation(run_path: Path, prepare: dict,
                             row: dict, proof_sha256: str) -> dict:
    authority = current_authority(row)
    if authority is None:
        raise ValueError(f"fid {row.get('fid')} has no current publication authority")
    item = next(
        (value for value in prepare["items"] if value["fid"] == row.get("fid")),
        None,
    )
    if item is None:
        raise ValueError(f"fid {row.get('fid')} is absent from terminal resolver run")
    receipt_path = run_path / "receipts" / f"chunk-{item['chunk']:02d}.json"
    receipt_bytes = resolver._read_bytes_nofollow(receipt_path)
    try:
        receipt = json.loads(receipt_bytes)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(
            f"fid {row.get('fid')} terminal receipt is not valid JSON"
        ) from error
    receipt_errors = resolver.validate_terminal_receipt(receipt)
    if receipt_errors:
        raise ValueError(
            f"fid {row.get('fid')} terminal receipt is malformed: {receipt_errors}"
        )
    output_seal_sha = receipt.get("output_seal_sha256")
    if not isinstance(output_seal_sha, str):
        raise ValueError(f"fid {row.get('fid')} terminal receipt has no output seal")
    bound = authority_wrapper(row)
    body = {
        "version": PUBLICATION_ATTESTATION_VERSION,
        "kind": PUBLICATION_ATTESTATION_KIND,
        "area": row["area"],
        "fid": row["fid"],
        "verdict": row["verdict"],
        "decision_sha256": tr.sha256_json(tr.decision_projection(row)),
        "evidence_sha256": tr.evidence_sha256(row),
        "packet_sha256": authority[2],
        "authority_kind": authority[0],
        "authority_sha256": authority[1],
        "resolver_run_id": prepare["run_id"],
        "output_seal_sha256": output_seal_sha,
        "terminal_receipt_sha256": tr.sha256_json(receipt),
        "publication_source_sha256": tr.sha256_json(publication_projection(row)),
        "publication_proof_sha256": proof_sha256,
        "review_receipt_sha256": (
            bound[1].get("review_receipt_sha256") if bound is not None else None
        ),
    }
    return {**body, "attestation_sha256": tr.sha256_json(body)}


def main(argv=None) -> int:
    """Run the CLI while owning every acquired lock in one cleanup scope."""
    lock_handles = []
    try:
        result = _main_under_lock_ownership(argv, lock_handles)
    except BaseException:
        try:
            _release_locks(lock_handles)
        except BaseException:
            # Preserve the command failure after attempting every cleanup.
            pass
        raise
    _release_locks(lock_handles)
    return result


def _main_under_lock_ownership(argv, area_locks: list) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    write = "--write" in argv
    argv = [a for a in argv if a != "--write"]
    sets_raw = _take_opt(argv, "--set", repeat=True)
    confirms_raw = _take_opt(argv, "--confirm", repeat=True)
    decide_raw = _take_opt(argv, "--decide")
    axes_raw = _take_opt(argv, "--axis", repeat=True)
    axis_evidence_raw = _take_opt(argv, "--axis-evidence", repeat=True)
    authority_run_raw = _take_opt(argv, "--authority-run")
    review_receipts_raw = _take_opt(argv, "--review-receipt", repeat=True)
    resolver_run_raw = _take_opt(argv, "--resolver-run")
    reviewer = _take_opt(argv, "--reviewer")
    note = _take_opt(argv, "--note")
    today = _take_opt(argv, "--judged")
    if (axes_raw or axis_evidence_raw) and not decide_raw:
        sys.exit("--axis and --axis-evidence require --decide")
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    store_path, slugs = Path(argv[0]), argv[1:]
    if sets_raw:
        sys.exit(
            "--set is disabled for new authority; use hash-bound --decide or --confirm"
        )
    if write and len(slugs) != 1:
        sys.exit("--write accepts exactly one area per atomic invocation")
    if resolver_run_raw and len(slugs) != 1:
        sys.exit("--resolver-run applies to exactly one area")
    if write and (confirms_raw or decide_raw):
        sys.exit("human authority and store publication require separate invocations")
    if review_receipts_raw and not (confirms_raw or decide_raw):
        sys.exit("--review-receipt requires --confirm or --decide")
    mutating = write or bool(confirms_raw) or bool(decide_raw)
    if mutating and today is None:
        sys.exit("--judged YYYY-MM-DD is required for human authority and publication")
    if today is not None:
        try:
            _dt.date.fromisoformat(today)
        except (TypeError, ValueError):
            sys.exit(f"--judged wants YYYY-MM-DD, got {today!r}")
    tmp = Path(PADJ_TMP)
    proof_registry_path = publication_proof_path(store_path)
    floor_registry_path = publication_floor_path(store_path)
    publication_paths = _publication_transaction_paths(
        store_path, proof_registry_path, "fixed",
        floor_path=floor_registry_path,
    )
    publication_journal = publication_paths["journal"]
    publication_configuration = None
    if write and resolver_run_raw:
        publication_configuration = (
            resolver.replay_trust.publication_configuration_for_store(
                store_path.name, store_path.parent
            )
        )

    if publication_configuration is not None:
        resource_modes = list(
            resolver.replay_trust.publication_resource_modes(
                store_path.parent, publication_configuration, store_path.name
            )
        )
        # The work-area dossier is a separate terminal input when the canonical
        # data directory differs from PADJ_TMP.
        resource_modes.append((
            tr.dossier_resource_path(tmp), fcntl.LOCK_SH
        ))
    else:
        exclusive_resources = (
            store_path, proof_registry_path, floor_registry_path,
            publication_journal,
        )
        shared_resources = [tr.dossier_resource_path(tmp)]
        baseline_resource = resolver.replay_trust.publication_baseline_path(
            store_path.name
        )
        if baseline_resource is not None:
            shared_resources.append(baseline_resource)
        canonical_exclusive = {
            Path(path).resolve() for path in exclusive_resources
        }
        canonical_shared = {Path(path).resolve() for path in shared_resources}
        if (len(canonical_exclusive) != len(exclusive_resources)
                or canonical_exclusive & canonical_shared):
            raise ValueError("publication resources have a canonical path collision")
        resource_modes = (
            [(path, fcntl.LOCK_EX) for path in exclusive_resources]
            + [(path, fcntl.LOCK_SH) for path in shared_resources]
        )
    resource_entries = trusted_fs.resource_lock_entries(
        tmp, resource_modes, tr.resource_lock_path,
    )
    for lock_path, _resource, mode in resource_entries:
        handle = resolver._open_lock_file(lock_path)
        area_locks.append(handle)
        fcntl.flock(handle.fileno(), mode)
    # Every global resource is sorted before any sorted area lock.
    for slug in sorted(set(slugs)):
        lock_path = tr.area_lock_path(tmp, slug)
        handle = resolver._open_lock_file(lock_path)
        area_locks.append(handle)
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
    if mutating:
        try:
            generation_captures = {
                slug: dossier_output.capture_generation_locked(tmp, slug)
                for slug in sorted(set(slugs))
            }
        except (OSError, ValueError, KeyError, TypeError) as error:
            print(f"source generation validation failed: {error}", file=sys.stderr)
            return 1
    else:
        generation_captures = {}
    live_publication_journal = os.path.lexists(publication_journal)
    if live_publication_journal and not write:
        print(
            "!! publication recovery required: a live journal may only be "
            "finished by the exact authorized --write invocation",
            file=sys.stderr,
        )
        return 1
    live_authority_journals = [
        tr.authority_journal_path(tmp, slug)
        for slug in slugs
        if tr.authority_journal_path(tmp, slug).exists()
    ]
    exact_authority_shape = (
        len(slugs) == 1
        and bool(confirms_raw) != bool(decide_raw)
        and not write
        and bool(authority_run_raw)
        and len(review_receipts_raw) == 1
        and isinstance(reviewer, str)
        and isinstance(today, str)
        and isinstance(note, str)
        and bool(note.strip())
    )
    if live_authority_journals and not exact_authority_shape:
        print(
            "RECOVERY_REQUIRED: a live human-authority journal may only be "
            "finished by the exact --confirm or --decide invocation",
            file=sys.stderr,
        )
        return 1
    try:
        store = json.loads(resolver._read_bytes_nofollow(store_path))
    except FileNotFoundError:
        store = {}
    if not isinstance(store, dict):
        raise ValueError("verdict store must contain an object")

    issues: list[str] = []
    staged_drafts: dict[Path, list[dict]] = {}
    staged_receipts: dict[str, dict] = {}
    terminal_prepares: dict[str, dict] = {}
    authority_prepare: dict | None = None
    authority_run: Path | None = None
    review_receipt_path: Path | None = None
    review_context: dict | None = None
    authority_request: dict | None = None
    authority_source_drafts: dict[Path, list[dict]] | None = None
    if decide_raw:
        if len(slugs) != 1:
            sys.exit("--decide applies to exactly one area at a time")
        if sets_raw or confirms_raw:
            sys.exit("--decide, --set, and --confirm use separate atomic invocations")
        if not isinstance(note, str) or not note.strip():
            sys.exit("--decide requires a non-empty --note")
        if not isinstance(reviewer, str) or tr.identity_key(reviewer) != reviewer:
            sys.exit("--decide requires a canonical --reviewer identity")
        if not authority_run_raw:
            sys.exit("--decide requires --authority-run PATH")
        fid_s, _, verdict = decide_raw.partition("=")
        if not fid_s.isdigit() or verdict not in ("KEEP", "DROP"):
            sys.exit(f"--decide wants FID=KEEP|DROP, got {decide_raw!r}")
        axis_calls: dict[str, str] = {}
        for value in axes_raw:
            axis, separator, call = value.partition("=")
            if not separator or axis not in ("exists", "public", "serves") or call not in CALLS:
                sys.exit(f"--axis wants exists|public|serves=yes|no|unclear|n/a, got {value!r}")
            axis_calls[axis] = call
        axis_evidence: dict[str, str] = {}
        for value in axis_evidence_raw:
            axis, separator, evidence = value.partition("=")
            if not separator or axis not in ("exists", "public", "serves") or not evidence.strip():
                sys.exit(f"--axis-evidence wants AXIS=TEXT, got {value!r}")
            axis_evidence[axis] = evidence
        if len(review_receipts_raw) != 1:
                sys.exit("--decide requires exactly one --review-receipt PATH")
        authority_run = Path(authority_run_raw).resolve()
        review_receipt_path = Path(review_receipts_raw[0])
        print("=== bound human decision ===")
        try:
            authority_prepare = _load_authority_prepare(
                authority_run, slugs[0]
            )
            review_context = resolver.load_review_receipt(
                review_receipt_path, authority_run, slugs[0],
                expected_fid=int(fid_s),
            )
            review_bindings = _review_bindings(
                review_context, slugs[0], authority_run,
                authority_prepare,
                next(item for item in authority_prepare["items"]
                     if item["fid"] == int(fid_s)),
            )
            authority_request = _canonical_authority_request({
                "version": AUTHORITY_TRANSACTION_VERSION,
                "kind": AUTHORITY_REQUEST_KIND,
                "authority_kind": "override_v3",
                "verdict": verdict,
                "axis_calls": axis_calls,
                "axis_evidence": axis_evidence,
                "reviewer": reviewer,
                "date": today,
                "note": note,
                "review_receipt_sha256": review_bindings[
                    "review_receipt_sha256"
                ],
                "review_sheet_sha256": review_bindings[
                    "review_sheet_sha256"
                ],
                "review_item_sha256": review_bindings[
                    "review_item_sha256"
                ],
            })
            authority_source_drafts = _authority_frozen_source_drafts(
                tmp, slugs[0], authority_run, authority_prepare
            )
            authority_lines = apply_bound_decision(
                tmp, slugs[0], int(fid_s), verdict,
                axis_calls, axis_evidence, note, today, reviewer,
                authority_run, review_receipt_path,
                staged_drafts=staged_drafts,
                staged_receipts=staged_receipts,
                source_drafts=authority_source_drafts,
                check_live_source=False,
            )
        except (OSError, ValueError, KeyError, TypeError, StopIteration,
                json.JSONDecodeError) as error:
            authority_lines = [f"  !! cannot reconstruct human decision: {error}"]
        for line in authority_lines:
            print(line)
            if line.lstrip().startswith("!!"):
                issues.append(line.strip())
    if sets_raw:
        if len(slugs) != 1:
            sys.exit("--set applies to exactly one area at a time")
        sets: dict[int, str] = {}
        for s in sets_raw:
            fid_s, _, verdict = s.partition("=")
            if not fid_s.isdigit() or verdict not in VERDICTS:
                sys.exit(f"--set wants FID=KEEP|DROP|REVIEW, got {s!r}")
            sets[int(fid_s)] = verdict
        print("=== human overrides ===")
        for line in apply_overrides(
            tmp, slugs[0], sets, note, today, staged_drafts
        ):
            print(line)
            if line.lstrip().startswith("!!"):
                issues.append(line.strip())
    if confirms_raw:
        if len(slugs) != 1:
            sys.exit("--confirm applies to exactly one area at a time")
        if sets_raw:
            sys.exit("--confirm and --set must use separate atomic invocations")
        if len(confirms_raw) != 1:
            sys.exit("--confirm accepts exactly one FID=VERDICT per atomic invocation")
        if not isinstance(note, str) or not note.strip():
            sys.exit("--confirm requires a non-empty --note")
        if not isinstance(reviewer, str) or tr.identity_key(reviewer) != reviewer:
            sys.exit("--confirm requires a canonical --reviewer identity")
        if not authority_run_raw:
            sys.exit("--confirm requires --authority-run PATH")
        confirmations: dict[int, str] = {}
        for value in confirms_raw:
            fid_s, _, verdict = value.partition("=")
            if not fid_s.isdigit() or verdict not in ("KEEP", "DROP"):
                sys.exit(f"--confirm wants FID=KEEP|DROP, got {value!r}")
            confirmations[int(fid_s)] = verdict
        if len(review_receipts_raw) != 1:
                sys.exit("--confirm requires exactly one --review-receipt PATH")
        authority_run = Path(authority_run_raw).resolve()
        review_receipt_path = Path(review_receipts_raw[0])
        print("=== human confirmations ===")
        try:
            authority_prepare = _load_authority_prepare(
                authority_run, slugs[0]
            )
            selected_fid = next(iter(confirmations))
            review_context = resolver.load_review_receipt(
                review_receipt_path, authority_run, slugs[0],
                expected_fid=selected_fid,
            )
            review_bindings = _review_bindings(
                review_context, slugs[0], authority_run,
                authority_prepare,
                next(item for item in authority_prepare["items"]
                     if item["fid"] == selected_fid),
            )
            authority_request = _canonical_authority_request({
                "version": AUTHORITY_TRANSACTION_VERSION,
                "kind": AUTHORITY_REQUEST_KIND,
                "authority_kind": "human_confirmation_v2",
                "verdict": next(iter(confirmations.values())),
                "axis_calls": {},
                "axis_evidence": {},
                "reviewer": reviewer,
                "date": today,
                "note": note,
                "review_receipt_sha256": review_bindings[
                    "review_receipt_sha256"
                ],
                "review_sheet_sha256": review_bindings[
                    "review_sheet_sha256"
                ],
                "review_item_sha256": review_bindings[
                    "review_item_sha256"
                ],
            })
            authority_source_drafts = _authority_frozen_source_drafts(
                tmp, slugs[0], authority_run, authority_prepare
            )
            authority_lines = apply_confirmations(
                tmp, slugs[0], confirmations, note, today, reviewer,
                authority_run, review_receipt_path,
                staged_drafts=staged_drafts,
                staged_receipts=staged_receipts,
                source_drafts=authority_source_drafts,
                check_live_source=False,
            )
        except (OSError, ValueError, KeyError, TypeError, StopIteration,
                json.JSONDecodeError) as error:
            authority_lines = [f"  !! cannot reconstruct confirmation: {error}"]
        for line in authority_lines:
            print(line)
            if line.lstrip().startswith("!!"):
                issues.append(line.strip())
    drops: list[tuple[str, dict, str]] = []
    entries: list[tuple[str, dict, dict]] = []      # (slug, verdict, facility)
    overlap_choices: dict[tuple[str, int], tuple[list[tuple[str, dict]], dict | None]] = {}
    totals = {v: 0 for v in VERDICTS}
    for slug in slugs:
        try:
            arr = load_staged_draft(tmp, slug, staged_drafts)
        except (ValueError, json.JSONDecodeError) as exc:
            issues.append(f"INVALID draft layout/content for {slug}: {exc}")
            continue
        if arr is None:
            issues.append(f"MISSING draft: {slug}")
            continue
        if write:
            run_path = Path(resolver_run_raw).resolve() if resolver_run_raw else None
            gate_errors, terminal_prepare = _terminal_resolver_errors(
                tmp, slug, arr, store, run_path,
                generation_captures[slug],
            )
            issues.extend(gate_errors)
            if terminal_prepare is not None:
                terminal_prepares[slug] = terminal_prepare
        bound_packets, packet_binding_issues = _bound_packets(
            tmp, slug, arr, staged_drafts=staged_drafts,
            terminal_prepare=terminal_prepares.get(slug),
            terminal_run=(Path(resolver_run_raw).resolve()
                          if slug in terminal_prepares else None),
        )
        issues.extend(packet_binding_issues)
        terminal_prepare = terminal_prepares.get(slug)
        if terminal_prepare is not None:
            publication = terminal_prepare["source"]["publication"]
            fac_by_fid = {
                int(fid): copy.deepcopy(facility)
                for fid, facility in publication["facilities"].items()
            }
            pub = list(publication["judge_fids"])
        elif mutating:
            # All authority/publication validation consumes the immutable
            # manifest-verified capture held by the outer inventory lease.
            dossier = dossier_output.captured_generation_document(
                generation_captures[slug], "dossier"
            )
            if (not isinstance(dossier, dict)
                    or not isinstance(dossier.get("facilities"), list)):
                raise ValueError(f"{slug}: captured dossier is malformed")
            fac_by_fid = {
                facility["fid"]: copy.deepcopy(facility)
                for facility in dossier["facilities"]
                if isinstance(facility, dict)
                and type(facility.get("fid")) is int
            }
            bound_prepare = (
                authority_prepare
                if isinstance(authority_prepare, dict)
                and authority_prepare.get("area") == slug
                else None
            )
            if bound_prepare is None:
                public_set_path = resolver._trusted_artifact_path(
                    tmp.resolve() / f"{slug}_pub.txt", tmp.resolve()
                )
                try:
                    pub = [
                        int(value) for value in resolver._read_bytes_nofollow(
                            public_set_path
                        ).decode("utf-8").split(",")
                        if value.strip()
                    ]
                except (OSError, UnicodeDecodeError, ValueError) as error:
                    issues.append(
                        f"{slug}: cannot capture the judge set: {error}"
                    )
                    pub = []
            else:
                try:
                    resolver.require_current_source_generation(
                        bound_prepare, generation_captures[slug]
                    )
                    pub = list(
                        bound_prepare["source"]["publication"]["judge_fids"]
                    )
                except (KeyError, TypeError, ValueError) as error:
                    issues.append(
                        f"{slug}: bound prepare source changed: {error}"
                    )
                    pub = []
        else:
            dos = json.load(open(tmp / f"{slug}_dossier.json"))
            fac_by_fid = {f["fid"]: f for f in dos["facilities"]}
            pub = [int(x) for x in open(tmp / f"{slug}_pub.txt").read().split(",") if x.strip()]
        seen: set[int] = set()
        counts = {v: 0 for v in VERDICTS}
        for e in arr:
            fid = e.get("fid")
            if fid in seen:
                issues.append(f"{slug} fid{fid}: judged twice")
                continue
            seen.add(fid)
            fac = fac_by_fid.get(fid)
            if fac is None:
                issues.append(f"{slug} fid{fid}: not in the dossier")
                continue
            packet_identity = bound_packets.get(fid) if _packet_bound(e) else None
            forbidden_persistence = sorted(
                field for field in (
                    "src", "judged", "lat", "lon", "rings", "name", "_key",
                    "reason", "publication_attestation",
                )
                if field in e
            )
            if forbidden_persistence:
                issues.append(
                    f"{slug} fid{fid}: draft contains host-owned persistence fields "
                    f"{forbidden_persistence}"
                )
            if packet_identity is None:
                packet_identity = {"fid": fid, "area": slug, "osm": fac.get("osm") or [],
                                   "prior": fac.get("prior")}
            row_issues, _ = validate_verdict_row(e, packet_identity)
            row_issues.extend(validate_authority_receipt(
                e, tmp, pending=staged_receipts
            ))
            bound_authority = authority_wrapper(e)
            if bound_authority is not None:
                receipt_sha = bound_authority[1].get("authority_receipt_sha256")
                receipt = staged_receipts.get(receipt_sha)
                if receipt is None and isinstance(receipt_sha, str):
                    receipt_path = authority_receipt_path(tmp, slug, receipt_sha)
                    try:
                        receipt = json.loads(receipt_path.read_text())
                    except (OSError, json.JSONDecodeError):
                        receipt = None
                if receipt is not None:
                    row_issues.extend(_validate_receipt_source(
                        receipt, e, packet_identity
                    ))
            issues.extend(f"{slug} fid{fid}: {issue}" for issue in row_issues)
            v = e.get("verdict")
            if v not in VERDICTS:
                continue
            if fid not in pub:
                issues.append(f"{slug} fid{fid}: not in the judge set (_pub.txt)")
            # Areas overlap; a lot judged under another area must not be
            # silently re-decided here. `judge_packets.py --skip-judged` keeps
            # such lots out of the judge set upstream — this is the backstop.
            holders = _foreign_holders(store, e.get("osm") or [], slug)
            preferred = _preferred_overlap(e, holders)
            overlap_choices[(slug, fid)] = (holders, preferred)
            issues.extend(_overlap_issues(slug, fid, e, holders))
            if (terminal_prepare is not None and preferred is not None
                    and preferred is not e
                    and _direct_human_source(preferred) is not None):
                candidate = copy.deepcopy(preferred)
                candidate_ids = list(candidate.get("osm") or [])
                for holder_id, held in holders:
                    for osm_id in [holder_id] + list(held.get("osm") or []):
                        if osm_id not in candidate_ids:
                            candidate_ids.append(osm_id)
                candidate["osm"] = candidate_ids
                candidate["lat"] = fac["lat"]
                candidate["lon"] = fac["lon"]
                candidate["rings"] = copy.deepcopy(fac["rings"])
                candidate["name"] = fac.get("name")
                all_aliases_already_bound = all(
                    isinstance(store.get(osm_id), dict)
                    and tr.sha256_json(store[osm_id]) == tr.sha256_json(preferred)
                    for osm_id in candidate_ids
                )
                if candidate != preferred or not all_aliases_already_bound:
                    issues.append(
                        f"{slug} fid{fid}: terminal publication would mutate an "
                        "unattested direct-human holder; migrate it to receipt-bound authority first"
                    )
            counts[v] += 1
            name = fac.get("name") or (fac.get("tags_union") or {}).get("name") or "(unnamed)"
            if v == "DROP":
                drops.append((slug, e, name))
            entries.append((slug, e, fac))
        missing = sorted(set(pub) - seen)
        if missing:
            issues.append(f"{slug}: NOT judged fids {missing}")
        for v in VERDICTS:
            totals[v] += counts[v]
        print(f"  {slug:40} {counts}")
    print(f"  {'TOTAL':40} {totals}  (n={sum(totals.values())})")

    print(f"\n=== {len(drops)} DROPS — eyeball these ===")
    for slug, e, name in drops:
        fid = e["fid"]
        print(f"\n  [{slug}] #{fid} '{name}' · prior={e.get('prior')} · conf={e.get('confidence')}")
        for ax in ("exists", "public", "serves"):
            a = e.get(ax) or {}
            print(f"     {ax.upper():6} {a.get('call')}: {a.get('evidence')}")
        print(f"     tile: {tmp / f'{slug}_ladder/{fid:04d}_z2.png'}")
    susp = [(s, e, n) for s, e, n in drops if e.get("prior") == "surveyed"]
    print(f"\n=== {len(susp)} SURVEYED-prior DROPS (canopy must NOT be the reason) ===")
    for slug, e, name in susp:
        print(f"  [{slug}] #{e['fid']} '{name}': {(e.get('exists') or {}).get('evidence')}")
    reviews = [(s, e) for s, e, _ in entries if e.get("verdict") == "REVIEW"]
    if reviews:
        print(f"\n=== {len(reviews)} REVIEWS ===")
        for slug, e in reviews:
            print(f"  [{slug}] #{e['fid']}: {e.get('resolve_hint')}")

    if issues:
        print("\n=== ISSUES ===")
        for i in issues:
            print("  " + i)
    else:
        print("\n=== no schema issues ===")
    if not write:
        if not issues and staged_drafts:
            if (authority_run is None or authority_prepare is None
                    or authority_request is None):
                issues.append("authority transaction context is incomplete")
            else:
                try:
                    _commit_authority_changes(
                        staged_drafts, staged_receipts, tmp, slugs[0],
                        authority_run, authority_prepare, authority_request,
                        generation_captures[slugs[0]],
                    )
                except (OSError, ValueError, KeyError, TypeError,
                        json.JSONDecodeError) as error:
                    issues.append(str(error))
                    print(f"  !! {error}")
                    print(
                        f"RECOVERY_REQUIRED: human-authority transaction "
                        f"refused: {error}",
                        file=sys.stderr,
                    )
        if live_authority_journals and (issues or not staged_drafts):
            print(
                "RECOVERY_REQUIRED: live human-authority journal remains; "
                "no canonical recovery mutation was made",
                file=sys.stderr,
            )
        return 1 if issues or (live_authority_journals and not staged_drafts) else 0
    if issues:
        if live_publication_journal:
            print(
                "\n!! publication recovery required: the exact terminal resolver "
                "gate did not authorize this --write invocation",
                file=sys.stderr,
            )
        else:
            print("\n!! refusing to --write with issues outstanding", file=sys.stderr)
        return 1
    if live_publication_journal and not terminal_prepares:
        print(
            "\n!! publication recovery required: the exact terminal resolver "
            "gate did not produce an authorized publication plan",
            file=sys.stderr,
        )
        return 1

    proof_registry_path = publication_proof_path(store_path)
    proof_registry = _load_publication_proofs(proof_registry_path)
    publication_proof_sha_by_slug = {}
    publication_authorization_by_slug = {}
    for slug, prepare in terminal_prepares.items():
        run_path = Path(resolver_run_raw).resolve()
        proof_sha, proof = _build_publication_proof(
            run_path, prepare, tmp,
            load_staged_draft(tmp, slug, staged_drafts) or [],
        )
        existing_proof = proof_registry["proofs"].get(proof_sha)
        if existing_proof is not None and existing_proof != proof:
            raise ValueError("publication proof hash collision or registry drift")
        proof_registry["proofs"][proof_sha] = proof
        publication_proof_sha_by_slug[slug] = proof_sha
        publication_authorization_by_slug[slug] = (
            _publication_authorization_context(
                run_path, prepare, proof_sha, proof
            )
        )

    before = len(store)
    kept = 0
    for slug, e, fac in entries:
        rec = dict(e)
        rec["area"] = slug
        rec.setdefault("src", "judge-fanout")
        holders, preferred = overlap_choices.get((slug, e.get("fid")), ([], None))
        same_area_prev = next(
            (store[osm_id] for osm_id in (e.get("osm") or [])
             if osm_id in store and store[osm_id].get("area") in (None, slug)),
            None,
        )
        if holders and preferred is not e:
            selected = copy.deepcopy(preferred)
            selected_ids = list(selected.get("osm") or [])
            for osm_id in e.get("osm") or []:
                if osm_id not in selected_ids:
                    selected_ids.append(osm_id)
            for holder_id, held in holders:
                for osm_id in [holder_id] + list(held.get("osm") or []):
                    if osm_id not in selected_ids:
                        selected_ids.append(osm_id)
            selected["osm"] = selected_ids
            if _direct_human_source(selected) is not None and slug in terminal_prepares:
                selected["lat"] = fac["lat"]
                selected["lon"] = fac["lon"]
                selected["rings"] = copy.deepcopy(fac["rings"])
                selected["name"] = fac.get("name")
            for osm_id in selected_ids:
                store[osm_id] = selected
            kept += 1
            continue
        prev = holders[0][1] if holders else same_area_prev
        rec.setdefault("judged", (prev or {}).get("judged") or today)
        rec["lat"] = round(float(fac["lat"]), 6)
        rec["lon"] = round(float(fac["lon"]), 6)
        rings = fac.get("rings") or ([fac["ring"]] if fac.get("ring") else [])
        rec["rings"] = [[[round(float(p[0]), 6), round(float(p[1]), 6)] for p in ring]
                        for ring in rings if ring]
        rec["name"] = fac.get("name") or (fac.get("tags_union") or {}).get("name")
        terminal_prepare = terminal_prepares.get(slug)
        # The store's ids first, then the rest of the cluster.
        ids = list(rec.get("osm") or [])
        ids += [o for o in fac.get("osm") or [] if o not in ids]
        for holder_id, held in holders:
            for osm_id in [holder_id] + list(held.get("osm") or []):
                if osm_id not in ids:
                    ids.append(osm_id)
        rec["osm"] = ids
        if terminal_prepare is not None:
            rec["publication_attestation"] = _publication_attestation(
                Path(resolver_run_raw).resolve(), terminal_prepare, rec,
                publication_proof_sha_by_slug[slug],
            )
            attestation_errors = validate_publication_attestation(rec)
            if attestation_errors:
                raise ValueError(
                    f"fid {rec.get('fid')} publication attestation invalid: {attestation_errors}"
                )
        for oid in ids:
            store[oid] = rec
    if terminal_prepares:
        referenced_proofs = {
            row.get("publication_attestation", {}).get("publication_proof_sha256")
            for row in store.values() if isinstance(row, dict)
            and isinstance(row.get("publication_attestation"), dict)
        }
        referenced_proofs.discard(None)
        proof_registry["proofs"] = {
            proof_sha: proof_registry["proofs"][proof_sha]
            for proof_sha in sorted(referenced_proofs)
            if proof_sha in proof_registry["proofs"]
        }
        if set(proof_registry["proofs"]) != referenced_proofs:
            raise ValueError("publication proof registry is missing a referenced proof")
        validated_rows = set()
        for row in store.values():
            if not isinstance(row, dict) or current_authority(row) is None:
                continue
            identity = tr.sha256_json(row)
            if identity in validated_rows:
                continue
            validated_rows.add(identity)
            attestation_errors = validate_publication_attestation(row)
            proof_errors = resolver.replay_trust._validate_publication_proof(
                row, proof_registry
            )
            if attestation_errors or proof_errors:
                raise ValueError(
                    f"publication proof precommit failed: {attestation_errors + proof_errors}"
                )
        try:
            _commit_publication(
                store_path, store, proof_registry_path, proof_registry,
                publication_authorization_by_slug[slugs[0]],
                publication_configuration=publication_configuration,
            )
        except (OSError, ValueError, json.JSONDecodeError) as error:
            print(
                f"\n!! publication recovery required: {error}",
                file=sys.stderr,
            )
            return 1
    else:
        resolver._atomic_bytes(
            store_path,
            json.dumps(
                store, indent=0, ensure_ascii=False, allow_nan=False
            ).encode("utf-8"),
        )
    print(f"\nWROTE {store_path}: {before} -> {len(store)} osm keys "
          f"({len(entries) - kept} verdicts merged"
          + (f", {kept} already held by another area, kept)" if kept else ")"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
