#!/usr/bin/env python3
"""Pure construction and validation of frozen human-review evidence.

This module performs no filesystem writes. Callers provide an artifact reader,
which lets the same deterministic builder protect local authority and replay
publication-proof bytes without trusting mutable paths.
"""
from __future__ import annotations

import base64
import copy
import hashlib
import html
import json
import sys
from pathlib import Path, PurePosixPath
from typing import Callable

import trust_resolution as tr

REVIEW_RECEIPT_VERSION = 1
REVIEW_RECEIPT_KIND = "parking-human-review-receipt"
REVIEW_FORMAT_VERSION = 1
REVIEW_ARTIFACT_DIR = ".review-artifacts"
REVIEW_SHEET_NAME = "review.html"
REVIEW_RECEIPT_NAME = "review-receipt.json"
SAMPLE_VERSION = 1
SAMPLE_STRATEGY = "sha256-run-fid-keep-v1"
_ZOOMS = ("z1", "z2", "z3")
_ORDER = {"DROP": 0, "REVIEW": 1, "SAMPLE": 2, "KEEP": 3}
_SHA_FIELDS = (
    "prepared_item_sha256", "packet_sha256", "packet_file_sha256",
    "source_draft_sha256", "source_checkpoint_sha256",
    "source_decision_sha256", "primary_envelope_sha256",
    "publication_facility_sha256", "review_item_sha256",
)


def json_bytes(value: object) -> bytes:
    return (json.dumps(
        value, indent=1, ensure_ascii=False, sort_keys=True, allow_nan=False
    ) + "\n").encode("utf-8")


def artifact_paths(tmp: str | Path, area: str, run_id: str) -> dict[str, Path]:
    if tr.area_slug(area) != area:
        raise ValueError("review artifacts require a canonical area slug")
    if not isinstance(run_id, str) or len(run_id) != 64 or any(
            character not in "0123456789abcdef" for character in run_id):
        raise ValueError("review artifacts require a lowercase run SHA-256")
    root = Path(tmp).expanduser().resolve()
    directory = root / REVIEW_ARTIFACT_DIR / area / run_id
    return {
        "root": root,
        "directory": directory,
        "sheet": directory / REVIEW_SHEET_NAME,
        "receipt": directory / REVIEW_RECEIPT_NAME,
    }


def review_item(receipt: dict, fid: int) -> dict:
    items = receipt.get("items") if isinstance(receipt, dict) else None
    if not isinstance(items, list):
        raise ValueError("review receipt item vector is unavailable")
    matches = [item for item in items if isinstance(item, dict) and item.get("fid") == fid]
    if len(matches) != 1:
        raise ValueError(f"review receipt does not contain exactly one fid {fid}")
    return matches[0]


def _relative_artifact(value: object, run_path: Path, label: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} path is invalid")
    candidate = Path(value)
    if candidate.is_absolute():
        try:
            relative = candidate.relative_to(run_path)
        except ValueError as error:
            raise ValueError(f"{label} path is outside the frozen run") from error
        if candidate != run_path / relative:
            raise ValueError(f"{label} path is noncanonical")
    else:
        pure = PurePosixPath(value)
        if (pure.is_absolute() or not pure.parts
                or any(part in ("", ".", "..") for part in pure.parts)):
            raise ValueError(f"{label} path is noncanonical")
        relative = Path(*pure.parts)
    normalized = relative.as_posix()
    if normalized != str(PurePosixPath(normalized)):
        raise ValueError(f"{label} path is noncanonical")
    return normalized


def _reject_duplicate_keys(pairs: list[tuple[str, object]]) -> dict:
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError(f"duplicate JSON key {key!r}")
        value[key] = item
    return value


def _parse_json(raw: bytes, expected: type, label: str):
    try:
        value = json.loads(raw, object_pairs_hook=_reject_duplicate_keys)
    except (UnicodeDecodeError, ValueError) as error:
        raise ValueError(f"{label} is not valid strict JSON: {error}") from error
    if not isinstance(value, expected):
        raise ValueError(f"{label} must contain a {expected.__name__}")
    return value


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _canonical_absolute_path(value: object) -> bool:
    if not isinstance(value, str):
        return False
    path = PurePosixPath(value)
    return (
        path.is_absolute()
        and str(path) == value
        and all(part not in ("", ".", "..") for part in path.parts[1:])
    )


def _nonnegative_index(value: object) -> bool:
    return type(value) is int and 0 <= value <= sys.maxsize


def _sample(prepare: dict, requested: int) -> dict:
    if type(requested) is not int or requested < 0:
        raise ValueError("review sample size must be a nonnegative integer")
    eligible = sorted(
        item["fid"] for item in prepare["items"]
        if item["primary"]["decision"].get("verdict") == "KEEP"
    )
    ranked = sorted(
        eligible,
        key=lambda fid: (
            hashlib.sha256(
                f"{SAMPLE_STRATEGY}:{prepare['run_id']}:{fid}".encode("utf-8")
            ).hexdigest(),
            fid,
        ),
    )
    return {
        "version": SAMPLE_VERSION,
        "strategy": SAMPLE_STRATEGY,
        "requested": requested,
        "eligible_fids": eligible,
        "selected_fids": sorted(ranked[:min(requested, len(ranked))]),
    }


def _axis_row(name: str, value: object) -> str:
    if not isinstance(value, dict):
        return f"<tr><th>{name}</th><td class='call'>-</td><td>-</td></tr>"
    call = str(value.get("call") or "-")
    css_call = html.escape(call).replace("/", "")
    return (
        f"<tr><th>{name}</th><td class='call {css_call}'>"
        f"{html.escape(call)}</td><td>{html.escape(str(value.get('evidence') or ''))}</td></tr>"
    )


def _card(detail: dict, sampled: bool) -> str:
    packet = detail["packet"]
    decision = detail["primary"]["decision"]
    facility = detail["publication_facility"]
    fid = detail["fid"]
    verdict = decision.get("verdict") or "REVIEW"
    shown = "SAMPLE" if sampled else verdict
    tags = packet.get("tags_union") if isinstance(packet.get("tags_union"), dict) else {}
    name = facility.get("name") or tags.get("name") or tags.get("operator") or "(unnamed)"
    images = []
    captions = {"z1": "Z1 · 600 m", "z2": "Z2 · 220 m", "z3": "Z3 · 140 m"}
    for zoom in _ZOOMS:
        encoded = base64.b64encode(detail["tile_bytes"][zoom]).decode("ascii")
        uri = f"data:image/png;base64,{encoded}"
        images.append(
            f"<figure class='{zoom}'><a href='{uri}' target='_blank' "
            f"rel='noopener'><img loading='lazy' src='{uri}' "
            f"alt='fid {fid} {zoom}'></a><figcaption>{captions[zoom]}</figcaption></figure>"
        )
    serves = packet.get("serves") if isinstance(packet.get("serves"), dict) else {}
    walk = packet.get("walk") if isinstance(packet.get("walk"), dict) else {}
    context = packet.get("context") if isinstance(packet.get("context"), dict) else {}
    frozen_facts = {
        "packet": {
            key: value for key, value in packet.items() if key != "tiles"
        },
        "publication_facility": facility,
    }
    frozen_facts_html = html.escape(
        tr.canonical_json(frozen_facts).decode("utf-8"), quote=True
    )
    facts = [
        f"prior <b>{html.escape(str(packet.get('prior') or ''))}</b>",
        f"coordinates {html.escape(str(facility.get('lat')))}, {html.escape(str(facility.get('lon')))}",
        "OSM " + html.escape(", ".join(str(value) for value in facility.get("osm") or [])),
        f"area {html.escape(str(packet.get('area_m2')))} m²" if packet.get("area_m2") else "node lot",
        f"serves <b>{html.escape(str(serves.get('trail') or ''))}</b> edge {html.escape(str(serves.get('edge_m')))} m",
        f"walk {html.escape(str(walk.get('walk_m')))} m" + ("" if walk.get("conn") else " (no foot route)"),
        f"context {html.escape(str(context.get('category') or ''))} {html.escape(str(context.get('evidence') or ''))}".rstrip(),
    ]
    hint = decision.get("resolve_hint")
    coverage = decision.get("coverage_gap")
    extra = ""
    if hint:
        extra += f"<p class='hint'><b>resolve:</b> {html.escape(str(hint))}</p>"
    if coverage:
        extra += "<p class='hint'><b>coverage gap</b> flagged</p>"
    return f"""
<section class='card {html.escape(str(verdict))} {shown}' data-verdict='{shown}' id='fid{fid}'>
  <header><span class='badge'>{html.escape(str(verdict))}{' · SAMPLE' if sampled else ''}</span>
    <span class='conf'>{html.escape(str(decision.get('confidence') or ''))}</span>
    <span class='fid'>#{fid}</span><span class='name'>{html.escape(str(name))}</span></header>
  <div class='body'><div class='frames'>{''.join(images)}</div><div class='text'>
    <ul class='facts'>{''.join(f'<li>{fact}</li>' for fact in facts)}</ul>
    <details class='frozen-facts' open><summary>Frozen packet and publication facts · canonical JSON</summary><pre>{frozen_facts_html}</pre></details>
    <table class='axes'>{_axis_row('EXISTS', decision.get('exists'))}{_axis_row('PUBLIC', decision.get('public'))}{_axis_row('SERVES', decision.get('serves'))}</table>{extra}
  </div></div>
</section>"""


_CSS = """
body{font:14px/1.4 -apple-system,Helvetica,Arial,sans-serif;margin:0;background:#f4f4f2;color:#222}
.top{position:sticky;top:0;background:#fff;border-bottom:1px solid #ccc;padding:10px 16px;z-index:9;display:flex;gap:10px;align-items:center;flex-wrap:wrap}
.top h1{font-size:16px;margin:0 12px 0 0}.meta{font:11px Menlo,monospace;color:#555;overflow-wrap:anywhere}
.top button{border:1px solid #999;background:#fafafa;border-radius:4px;padding:4px 10px;cursor:pointer}.top button.on{background:#222;color:#fff;border-color:#222}
.card{background:#fff;margin:14px 16px;border-radius:8px;box-shadow:0 1px 3px rgba(0,0,0,.15);border-left:8px solid #999}
.card.SAMPLE{border-left-color:#2980b9}.card.DROP{border-left-color:#c0392b}.card.KEEP{border-left-color:#27ae60}.card.REVIEW{border-left-color:#e67e22}
.card header{display:flex;gap:12px;align-items:baseline;padding:10px 14px;border-bottom:1px solid #eee;flex-wrap:wrap}.badge{font-weight:700;padding:2px 8px;border-radius:4px;color:#fff;background:#999}
.DROP .badge{background:#c0392b}.KEEP .badge{background:#27ae60}.REVIEW .badge{background:#e67e22}.conf{color:#666}.fid{font-family:Menlo,monospace}.name{font-weight:600}
.body{display:flex;gap:14px;padding:12px 14px;flex-wrap:wrap}.frames{display:flex;gap:8px;align-items:flex-start}figure{margin:0;text-align:center;font-size:12px;color:#666}
figure img{display:block;border:1px solid #ddd;border-radius:4px}.z1 img,.z3 img{width:360px}.z2 img{width:560px}.text{flex:1;min-width:360px}.facts{margin:0 0 10px;padding-left:18px;color:#444}
.frozen-facts{margin:0 0 10px}.frozen-facts summary{cursor:pointer;font-weight:600}.frozen-facts pre{white-space:pre-wrap;overflow-wrap:anywhere;background:#f7f7f7;border:1px solid #ddd;border-radius:4px;padding:8px;font:11px/1.35 Menlo,monospace}
.axes{border-collapse:collapse;width:100%}.axes th{text-align:left;vertical-align:top;padding:4px 8px 4px 0;width:64px}.axes td{vertical-align:top;padding:4px 6px;border-top:1px solid #eee}.call{font-weight:700;width:60px}.call.no{color:#c0392b}.call.yes{color:#27ae60}.call.unclear{color:#e67e22}.hint{background:#fff6e5;padding:6px 10px;border-radius:4px}.hidden{display:none}
"""

_JS = """
const btns=[...document.querySelectorAll('.top button[data-f]')];
function apply(f){document.querySelectorAll('.card').forEach(c=>c.classList.toggle('hidden',f!=='ALL'&&c.dataset.verdict!==f));btns.forEach(b=>b.classList.toggle('on',b.dataset.f===f));location.hash='';}
btns.forEach(b=>b.addEventListener('click',()=>apply(b.dataset.f)));apply(new URLSearchParams(location.search).get('f')||'ALL');
"""


def _render(prepare: dict, details: list[dict], sample: dict,
            prepare_sha256: str) -> bytes:
    sampled = set(sample["selected_fids"])
    counts = {verdict: 0 for verdict in ("DROP", "REVIEW", "KEEP")}
    ordered = []
    for detail in details:
        verdict = detail["primary"]["decision"].get("verdict") or "REVIEW"
        counts[verdict] = counts.get(verdict, 0) + 1
        shown = "SAMPLE" if detail["fid"] in sampled else verdict
        ordered.append((_ORDER.get(shown, 9), detail["fid"], detail))
    ordered.sort(key=lambda value: (value[0], value[1]))
    counts["SAMPLE"] = len(sampled)
    summary = " · ".join(
        f"{key} {counts[key]}" for key in ("DROP", "REVIEW", "KEEP") if counts[key]
    )
    if sampled:
        summary += f" · {len(sampled)} KEEPs sampled"
    buttons = [f"<button data-f='ALL'>ALL ({len(details)})</button>"]
    buttons.extend(
        f"<button data-f='{key}'>{key} ({counts[key]})</button>"
        for key in ("DROP", "REVIEW", "SAMPLE", "KEEP") if counts.get(key)
    )
    cards = "".join(_card(detail, detail["fid"] in sampled) for _, _, detail in ordered)
    area = html.escape(prepare["area"])
    run_id = html.escape(prepare["run_id"])
    return f"""<!doctype html><html><head><meta charset='utf-8'>
<title>{area} frozen parking review</title><style>{_CSS}</style></head><body>
<div class='top'><h1>{area}</h1><span>{len(details)} lots · {summary}</span>{''.join(buttons)}
<span class='meta'>authority run {run_id} · prepare {prepare_sha256}</span>
<span style='margin-left:auto;color:#666'>Authority is limited to the embedded frozen PNGs and frozen decisions. OSM IDs and coordinates are labels, not live evidence.</span></div>
{cards}<script>{_JS}</script></body></html>""".encode("utf-8")


def build_review_artifacts(
        prepare: dict, prepare_bytes: bytes, run_path: Path,
        read_artifact: Callable[[str], bytes], sample_requested: int,
        sheet_path: Path) -> tuple[bytes, dict, list[str]]:
    """Return exact sheet bytes, self-hashed receipt, and consumed run files."""
    expected_run = Path(prepare["run_root"]) / prepare["run_id"]
    if (not run_path.is_absolute() or run_path != expected_run
            or run_path.name != prepare["run_id"]):
        raise ValueError("review source run path is noncanonical")
    parsed_prepare = _parse_json(prepare_bytes, dict, "prepare.json")
    if parsed_prepare != prepare:
        raise ValueError("review prepare bytes do not match the validated document")
    if prepare_bytes != json_bytes(prepare):
        raise ValueError("review prepare bytes are noncanonical")
    consumed: list[str] = []

    def read(relative: str) -> bytes:
        canonical = _relative_artifact(relative, run_path, "frozen artifact")
        raw = read_artifact(canonical)
        if not isinstance(raw, bytes):
            raise ValueError(f"frozen artifact reader returned non-bytes for {canonical}")
        consumed.append(canonical)
        return raw

    source_packets = read(prepare["source"]["packets_frozen_path"])
    if _sha(source_packets) != prepare["source"]["packets_file_sha256"]:
        raise ValueError("frozen source packet bytes changed")
    source_by_chunk = {source["chunk"]: source for source in prepare["source"]["drafts"]}
    source_rows: dict[int, list] = {}
    for chunk, source in sorted(source_by_chunk.items()):
        draft_bytes = read(source["frozen_path"])
        checkpoint_bytes = read(source["checkpoint_frozen_path"])
        if (_sha(draft_bytes) != source["file_sha256"]
                or _sha(checkpoint_bytes) != source["checkpoint_sha256"]):
            raise ValueError(f"frozen source chunk {chunk:02d} changed")
        source_rows[chunk] = _parse_json(draft_bytes, list, f"source draft {chunk:02d}")
        checkpoint = _parse_json(checkpoint_bytes, dict, f"source checkpoint {chunk:02d}")
        if checkpoint != source["checkpoint_value"]:
            raise ValueError(f"frozen source checkpoint {chunk:02d} value changed")

    details = []
    receipt_items = []
    facilities = prepare["source"]["publication"]["facilities"]
    for item in sorted(prepare["items"], key=lambda value: value["fid"]):
        fid = item["fid"]
        packet_relative = _relative_artifact(item["packet_path"], run_path, f"fid {fid} packet")
        packet_bytes = read(packet_relative)
        packet = _parse_json(packet_bytes, dict, f"fid {fid} packet")
        tiles = packet.get("tiles")
        if not isinstance(tiles, dict) or set(tiles) != set(_ZOOMS):
            raise ValueError(f"fid {fid} frozen tile set is malformed")
        tile_hashes = {}
        tile_bytes = {}
        for zoom in _ZOOMS:
            relative = _relative_artifact(tiles[zoom], run_path, f"fid {fid} {zoom}")
            raw = read(relative)
            if not raw.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError(f"fid {fid} {zoom} is not frozen PNG data")
            tile_bytes[zoom] = raw
            tile_hashes[zoom] = _sha(raw)
        payload = {key: copy.deepcopy(value) for key, value in packet.items() if key != "tiles"}
        packet_sha = tr.sha256_json({"packet": payload, "tile_sha256": tile_hashes})
        if packet_sha != item["packet_sha256"]:
            raise ValueError(f"fid {fid} frozen packet semantic hash changed")
        source = source_by_chunk[item["chunk"]]
        rows = source_rows[item["chunk"]]
        if item["row_index"] >= len(rows) or not isinstance(rows[item["row_index"]], dict):
            raise ValueError(f"fid {fid} frozen source row is unavailable")
        source_row = rows[item["row_index"]]
        if source_row.get("fid") != fid:
            raise ValueError(f"fid {fid} frozen source row index changed")
        primary = copy.deepcopy(item["primary"])
        facility = copy.deepcopy(facilities[str(fid)])
        receipt_item = {
            "fid": fid,
            "chunk": item["chunk"],
            "row_index": item["row_index"],
            "prepared_item_sha256": tr.sha256_json(item),
            "packet_sha256": packet_sha,
            "packet_file_sha256": _sha(packet_bytes),
            "tile_sha256": tile_hashes,
            "source_draft_sha256": source["file_sha256"],
            "source_checkpoint_sha256": source["checkpoint_sha256"],
            "source_decision_sha256": tr.sha256_json(
                tr.decision_projection(primary["decision"])
            ),
            "primary_envelope_sha256": primary["envelope_sha256"],
            "primary": primary,
            "publication_facility_sha256": tr.sha256_json(facility),
            "publication_facility": facility,
        }
        receipt_item["review_item_sha256"] = tr.sha256_json(receipt_item)
        receipt_items.append(receipt_item)
        details.append({
            "fid": fid,
            "packet": packet,
            "tile_bytes": tile_bytes,
            "primary": primary,
            "publication_facility": facility,
        })

    sample = _sample(prepare, sample_requested)
    prepare_sha = _sha(prepare_bytes)
    sheet_bytes = _render(prepare, details, sample, prepare_sha)
    body = {
        "version": REVIEW_RECEIPT_VERSION,
        "kind": REVIEW_RECEIPT_KIND,
        "format_version": REVIEW_FORMAT_VERSION,
        "area": prepare["area"],
        "source_run_id": prepare["run_id"],
        "source_run_path": str(run_path),
        "source_prepare_sha256": prepare_sha,
        "source_packets_sha256": prepare["source"]["packets_file_sha256"],
        "sample": sample,
        "items": receipt_items,
        "sheet_path": str(sheet_path),
        "sheet_sha256": _sha(sheet_bytes),
    }
    receipt_sha = tr.sha256_json(body)
    receipt = {**body, "receipt_sha256": receipt_sha}
    return sheet_bytes, receipt, sorted(set(consumed))


def validate_review_receipt(receipt: object) -> list[str]:
    required = {
        "version", "kind", "format_version", "area", "source_run_id",
        "source_run_path", "source_prepare_sha256", "source_packets_sha256",
        "sample", "items", "sheet_path", "sheet_sha256", "receipt_sha256",
    }
    if not isinstance(receipt, dict) or set(receipt) != required:
        return ["review receipt schema mismatch"]
    errors: list[str] = []
    if (type(receipt.get("version")) is not int
            or receipt.get("version") != REVIEW_RECEIPT_VERSION
            or receipt.get("kind") != REVIEW_RECEIPT_KIND
            or type(receipt.get("format_version")) is not int
            or receipt.get("format_version") != REVIEW_FORMAT_VERSION
            or tr.area_slug(receipt.get("area")) != receipt.get("area")):
        errors.append("review receipt version/kind/area mismatch")
    for field in (
        "source_run_id", "source_prepare_sha256", "source_packets_sha256",
        "sheet_sha256", "receipt_sha256",
    ):
        value = receipt.get(field)
        if not isinstance(value, str) or len(value) != 64 or any(
                character not in "0123456789abcdef" for character in value):
            errors.append(f"review receipt {field} is invalid")
    for field in ("source_run_path", "sheet_path"):
        value = receipt.get(field)
        if not _canonical_absolute_path(value):
            errors.append(f"review receipt {field} is noncanonical")
    sample = receipt.get("sample")
    sample_keys = {
        "version", "strategy", "requested", "eligible_fids", "selected_fids",
    }
    if (not isinstance(sample, dict) or set(sample) != sample_keys
            or type(sample.get("version")) is not int
            or sample.get("version") != SAMPLE_VERSION
            or sample.get("strategy") != SAMPLE_STRATEGY
            or type(sample.get("requested")) is not int
            or sample.get("requested") < 0):
        errors.append("review receipt sample is malformed")
    else:
        eligible = sample.get("eligible_fids")
        selected = sample.get("selected_fids")
        if (not isinstance(eligible, list) or not isinstance(selected, list)
                or any(not _nonnegative_index(fid) for fid in eligible + selected)
                or eligible != sorted(set(eligible))
                or selected != sorted(set(selected))
                or not set(selected).issubset(eligible)
                or len(selected) != min(sample["requested"], len(eligible))):
            errors.append("review receipt sample fid vectors are malformed")
    items = receipt.get("items")
    item_keys = {
        "fid", "chunk", "row_index", "prepared_item_sha256",
        "packet_sha256", "packet_file_sha256", "tile_sha256",
        "source_draft_sha256", "source_checkpoint_sha256",
        "source_decision_sha256", "primary_envelope_sha256", "primary",
        "publication_facility_sha256", "publication_facility",
        "review_item_sha256",
    }
    if not isinstance(items, list) or not items:
        errors.append("review receipt item vector is empty")
    else:
        fids: list[int] = []
        coordinate_valid_items: list[dict] = []
        for item in items:
            if not isinstance(item, dict) or set(item) != item_keys:
                errors.append("review receipt item schema mismatch")
                continue
            coordinates_valid = all(
                _nonnegative_index(item.get(field))
                for field in ("fid", "chunk", "row_index")
            )
            if not coordinates_valid:
                errors.append("review receipt item coordinates are invalid")
            else:
                fids.append(item["fid"])
                coordinate_valid_items.append(item)
            for field in _SHA_FIELDS:
                value = item.get(field)
                if not isinstance(value, str) or len(value) != 64 or any(
                        character not in "0123456789abcdef" for character in value):
                    errors.append(f"review receipt item {field} is invalid")
            tiles = item.get("tile_sha256")
            if (not isinstance(tiles, dict) or set(tiles) != set(_ZOOMS)
                    or any(not isinstance(value, str) or len(value) != 64
                           or any(character not in "0123456789abcdef" for character in value)
                           for value in tiles.values())):
                errors.append("review receipt item tile hashes are malformed")
            if not isinstance(item.get("primary"), dict):
                errors.append("review receipt item primary is malformed")
            if not isinstance(item.get("publication_facility"), dict):
                errors.append("review receipt item publication facility is malformed")
            body = dict(item)
            claimed = body.pop("review_item_sha256", None)
            try:
                expected_item_hash = tr.sha256_json(body)
            except (TypeError, ValueError):
                expected_item_hash = None
            if claimed != expected_item_hash:
                errors.append("review receipt item hash mismatch")
        if len(fids) != len(items) or fids != sorted(set(fids)):
            errors.append("review receipt item vector is not sorted and unique")
        if isinstance(sample, dict) and isinstance(sample.get("eligible_fids"), list):
            expected_eligible = sorted(
                item["fid"] for item in coordinate_valid_items
                if isinstance(item.get("primary"), dict)
                and isinstance(item["primary"].get("decision"), dict)
                and item["primary"]["decision"].get("verdict") == "KEEP"
            )
            if sample["eligible_fids"] != expected_eligible:
                errors.append("review receipt sample population mismatch")
    body = dict(receipt)
    claimed = body.pop("receipt_sha256", None)
    try:
        expected_receipt_hash = tr.sha256_json(body)
    except (TypeError, ValueError):
        expected_receipt_hash = None
    if claimed != expected_receipt_hash:
        errors.append("review receipt self-hash mismatch")
    return errors
