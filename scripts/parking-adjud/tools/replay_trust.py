#!/usr/bin/env python3
"""Read-only replay of Trekdex parking verdicts through the shadow trust policy.

The command reads authoritative stores, the generated sidecar, calibration
ledger, and historical label file.  It never edits drafts, stores, sidecars,
geom, pools, workflows, or live data.  JSON goes to stdout by default.  An
explicit report path is allowed only outside the repository or under the
ignored `scripts/parking-adjud/work/` tree.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[2]
SCRIPTS = ROOT / "scripts"
DATA = SCRIPTS / "parking-adjud" / "data"
WORK = SCRIPTS / "parking-adjud" / "work"
SIDECAR = ROOT / "public" / "areas" / "parking-verdicts.json"

for path in (str(TOOLS), str(SCRIPTS)):
    if path not in sys.path:
        sys.path.insert(0, path)

import trust_engine  # noqa: E402
from judge_validation import canonical_draft_files, load_canonical_drafts, validate_verdict_row  # noqa: E402


def _load_builder():
    path = SCRIPTS / "build-parking-verdicts.py"
    spec = importlib.util.spec_from_file_location("parking_verdict_builder_shadow", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_json(path: Path, expected: type) -> object:
    value = json.loads(path.read_text())
    if not isinstance(value, expected):
        raise ValueError(f"{path} must contain a {expected.__name__}")
    return value


def load_corpus(data_dir: Path = DATA, sidecar_path: Path = SIDECAR) -> tuple[list[dict], dict, dict]:
    """Load one canonical source row per builder-defined lot cluster.

    Identity and duplicate folding deliberately mirror build-parking-verdicts.py.
    Any placement, disagreement, or sidecar drift fails the replay instead of
    producing a comforting partial report.
    """
    builder = _load_builder()
    document, notes, folded = builder.build(str(data_dir))
    if notes:
        raise ValueError("source compiler rejected entries: " + " | ".join(notes))
    canonical_bytes = builder.serialize(document).encode("utf-8")
    committed_bytes = sidecar_path.read_bytes()
    if committed_bytes != canonical_bytes:
        raise ValueError(
            f"{sidecar_path} is not byte-exact canonical output of the authoritative stores"
        )
    committed = json.loads(committed_bytes)
    if not isinstance(committed, dict):
        raise ValueError(f"{sidecar_path} must contain an object")

    by_fid, by_osm = builder.load_dossiers(str(data_dir))
    claimed: dict[str, str] = {}
    items: dict[str, dict] = {}
    source_rows = 0
    corpus_hash = hashlib.sha256()

    for store, slug_hint, _judged in builder.STORES:
        path = data_dir / store
        raw = path.read_bytes()
        corpus_hash.update(store.encode("utf-8") + b"\0" + raw + b"\0")
        values = json.loads(raw)
        if not isinstance(values, dict):
            raise ValueError(f"{path} must contain an object")
        for source_key, row in values.items():
            if not isinstance(row, dict) or row.get("verdict") not in ("KEEP", "DROP", "REVIEW"):
                continue
            source_rows += 1
            facility = builder.facility_for(row, slug_hint, by_fid, by_osm)
            if facility is None:
                raise ValueError(f"{store}:{source_key} has no dossier position")
            osm = list(row.get("osm") or [])
            osm += [value for value in facility.get("osm") or [] if value not in osm]
            if not osm:
                raise ValueError(f"{store}:{source_key} has no OSM identity")
            duplicate = next((claimed[value] for value in osm if value in claimed), None)
            if duplicate is not None:
                if items[duplicate]["row"].get("verdict") != row.get("verdict"):
                    raise ValueError(
                        f"{store}:{source_key} disagrees with {duplicate} for one lot cluster"
                    )
                continue
            key = osm[0]
            if key not in document["lots"]:
                raise ValueError(f"{store}:{source_key} compiled key {key} is absent from the sidecar")
            item = {
                "key": key,
                "source_key": source_key,
                "store": store,
                "area": row.get("area") or facility.get("_slug"),
                "row": row,
                "published": document["lots"][key],
            }
            items[key] = item
            for value in osm:
                claimed[value] = key

    if set(items) != set(document.get("lots") or {}):
        missing = sorted(set(document.get("lots") or {}) - set(items))
        extra = sorted(set(items) - set(document.get("lots") or {}))
        raise ValueError(f"source/sidecar identity mismatch: missing={missing[:5]} extra={extra[:5]}")
    if source_rows - len(items) != len(folded):
        raise ValueError(
            f"fold accounting mismatch: {source_rows} source - {len(items)} unique != {len(folded)} folds"
        )

    metadata = {
        "corpus_sha256": corpus_hash.hexdigest(),
        "source_rows": source_rows,
        "unique_clusters": len(items),
        "folded_duplicates": len(folded),
        "sidecar_sha256": hashlib.sha256(sidecar_path.read_bytes()).hexdigest(),
    }
    return [items[key] for key in sorted(items)], metadata, document


def load_work_area(tmp: Path, slug: str) -> tuple[list[dict], dict, dict]:
    """Load a completed primary draft as a shadow routing queue.

    Packet coverage and identity are exact.  The result is never added to the
    authoritative store list and has no path to a publishing consumer.
    """
    packet_path = tmp / f"{slug}_packets.json"
    raw_packets = _load_json(packet_path, dict)
    packets: dict[int, dict] = {}
    for key, packet in raw_packets.items():
        if not isinstance(packet, dict):
            raise ValueError(f"{packet_path}: packet {key!r} is not an object")
        fid = packet.get("fid")
        if not isinstance(fid, int) or str(fid) != str(key):
            raise ValueError(f"{packet_path}: packet key/fid mismatch at {key!r}")
        if packet.get("area") != slug:
            raise ValueError(
                f"{packet_path}: packet {fid} area {packet.get('area')!r} does not match {slug!r}"
            )
        if fid in packets:
            raise ValueError(f"{packet_path}: duplicate fid {fid}")
        packets[fid] = packet
    drafts = load_canonical_drafts(tmp, slug)
    if drafts is None:
        raise ValueError(f"no canonical verdict drafts for {slug}")
    seen: set[int] = set()
    items = []
    corpus_hash = hashlib.sha256(packet_path.read_bytes())
    for draft_path in canonical_draft_files(tmp, slug):
        corpus_hash.update(draft_path.name.encode("utf-8") + b"\0" + draft_path.read_bytes())
    for row in drafts:
        fid = row.get("fid") if isinstance(row, dict) else None
        if not isinstance(fid, int) or fid in seen:
            raise ValueError(f"{slug}: invalid or duplicate draft fid {fid!r}")
        packet = packets.get(fid)
        if packet is None:
            raise ValueError(f"{slug}: draft fid {fid} has no packet")
        identity_errors, _ = validate_verdict_row(row, packet=packet)
        identity_errors = [error for error in identity_errors if "does not match the current packet" in error]
        if identity_errors:
            raise ValueError(f"{slug}: fid {fid}: " + "; ".join(identity_errors))
        osm = list(row.get("osm") or [])
        if not osm:
            raise ValueError(f"{slug}: fid {fid} has no OSM identity")
        key = osm[0]
        if any(key == item["key"] or set(osm) & set(item["row"].get("osm") or []) for item in items):
            raise ValueError(f"{slug}: duplicate OSM cluster in primary drafts at fid {fid}")
        seen.add(fid)
        published = {
            "verdict": row.get("verdict"),
            "area": slug,
            "src": row.get("src") or "shadow-primary",
        }
        items.append({
            "key": key,
            "source_key": str(fid),
            "store": f"work:{slug}",
            "area": slug,
            "row": row,
            "published": published,
        })
    missing = sorted(set(packets) - seen)
    if missing:
        raise ValueError(f"{slug}: primary drafts missing packet fids {missing}")
    document = {"version": 1, "lots": {item["key"]: item["published"] for item in items}}
    metadata = {
        "corpus_sha256": corpus_hash.hexdigest(),
        "source_rows": len(items),
        "unique_clusters": len(items),
        "folded_duplicates": 0,
        "sidecar_sha256": None,
        "work_area": slug,
    }
    return sorted(items, key=lambda item: item["key"]), metadata, document


def _is_within(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def validate_report_path(path: Path) -> Path:
    target = path.expanduser().resolve()
    if target.suffix.lower() != ".json":
        raise ValueError("--out must end in .json")
    if _is_within(target, ROOT.resolve()) and not _is_within(target, WORK.resolve()):
        raise ValueError("--out inside the repository is allowed only under scripts/parking-adjud/work/")
    return target


def write_report(path: Path, text: str) -> None:
    target = validate_report_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp-{os.getpid()}")
    temporary.write_text(text)
    os.replace(temporary, target)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--data-dir", type=Path, default=DATA)
    parser.add_argument("--sidecar", type=Path, default=SIDECAR)
    parser.add_argument("--work-area", metavar="SLUG",
                        help="route one completed primary draft from --tmp instead of committed stores")
    parser.add_argument("--tmp", type=Path,
                        help="packet/draft directory for --work-area")
    parser.add_argument("--ledger", type=Path, default=DATA / "calibration.json")
    parser.add_argument("--groundtruth", type=Path, default=DATA / "groundtruth.json")
    parser.add_argument("--format", choices=("json", "summary"), default="json")
    parser.add_argument("--include-items", action="store_true",
                        help="include one machine route record per canonical lot")
    parser.add_argument("--out", type=Path,
                        help="atomically write JSON under ignored work/ or outside the repository")
    args = parser.parse_args(argv)

    try:
        if bool(args.work_area) != bool(args.tmp):
            raise ValueError("--work-area and --tmp must be supplied together")
        ledger = _load_json(args.ledger, dict)
        if args.work_area:
            items, corpus, sidecar = load_work_area(args.tmp, args.work_area)
            groundtruth = {}
            include_items = True
        else:
            items, corpus, sidecar = load_corpus(args.data_dir, args.sidecar)
            groundtruth = _load_json(args.groundtruth, dict)
            include_items = args.include_items
        report = trust_engine.build_report(
            items, ledger, groundtruth, sidecar, corpus, include_items=include_items
        )
        text = json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
        if args.out is not None:
            write_report(args.out, text)
            print(f"wrote {validate_report_path(args.out)}")
        if args.format == "summary":
            print(trust_engine.summary_text(report))
        elif args.out is None:
            sys.stdout.write(text)
        return 0
    except (OSError, ValueError, json.JSONDecodeError) as error:
        print(f"trust replay failed: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
