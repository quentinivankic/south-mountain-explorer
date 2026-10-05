#!/usr/bin/env python3
"""Calibration ledger: how often does the human agree with the judge?

The judge fan-out produces verdicts; a human reviews some of them and records
receipt-bound changes with `merge_drafts.py --decide` or same-verdict
affirmations with `--confirm`. This ledger records, per area, what the judge
said (by verdict and confidence), which verdict classes the human reviewed one
lot at a time versus accepted en bloc, and every flip. The report turns that
into agreement rates with a 95% upper bound on the miss rate, per class, so
the question "is the judge good enough to stop reviewing every DROP?" gets a
number instead of a feeling.

    PADJ_TMP=work/co python3 tools/calibration.py add <slug> \
        --reviewed DROP=each --reviewed REVIEW=each --reviewed KEEP=sample \
        --sample-fids 3,17,42 [--spot FID ...] [--note TEXT] [--date YYYY-MM-DD]
    python3 tools/calibration.py report [--target 0.02] [--min-n 100]

`add` reads the area's verdict drafts after receipt-bound authority is applied
and appends one record to `data/calibration.json`; the judge is scored on its
ORIGINAL call via `original_judge_projection()` (`override.from_decision` for
versioned decisions). A class the human only accepted en bloc is recorded but not
counted as reviewed: silence is not agreement. `--reviewed KEEP=sample
--sample-fids 3,17,...` says the human went through exactly those lots one by
one (the sheet's SAMPLE cards); only they enter the KEEP denominator.
Re-adding an area replaces its record.

`report` prints, per (judge verdict, confidence): lots judged, lots the human
reviewed one at a time, flips, agreement, and the Wilson 95% upper bound on the
true miss rate. A class is marked AUTO-OK when at least `--min-n` lots were
reviewed and the bound is at or under `--target` (default 2%); otherwise it
says how many more zero-flip reviews would get it there. A flip inside a class
the human accepted en bloc still counts as a flip (evidence of a miss) even
though the class has no denominator. REVIEW is the judge deferring, not
calling: its resolutions are listed, never scored as misses.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import fcntl
import hashlib
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from judge_validation import (  # noqa: E402
    canonical_draft_files,
    machine_decision_projection,
    original_judge_projection,
)
import trust_resolution as tr  # noqa: E402
import review_evidence as review  # noqa: E402
import trusted_filesystem as trusted_fs  # noqa: E402

PADJ_TMP = os.environ.get("PADJ_TMP") or os.path.join(_HERE, "..", "work")
LEDGER = os.environ.get("PADJ_CALIBRATION") or os.path.join(_HERE, "..", "data", "calibration.json")
VERDICTS = ("KEEP", "DROP", "REVIEW")
CONFIDENCE = ("certain", "strong", "leaning")
REVIEW_MODES = ("each", "sample", "en-bloc", "none")
Z95 = 1.959964


def load_drafts(tmp: str, slug: str) -> list[dict]:
    try:
        files = canonical_draft_files(tmp, slug)
    except ValueError as exc:
        sys.exit(str(exc))
    if not files:
        sys.exit(f"no verdict drafts for {slug} under {tmp}")
    out: list[dict] = []
    for path in files:
        value = json.loads(path.read_text())
        if not isinstance(value, list):
            sys.exit(f"{path} must contain a JSON list")
        out.extend(value)
    return out


def judge_call(e: dict) -> tuple[str, str]:
    """Return the strict original judge call before any human override.

    Calibration is a quality gate, so malformed override provenance must fail
    closed rather than falling back to the human-updated confidence bucket.
    """
    projected, errors = original_judge_projection(e)
    if errors or projected is None:
        fid = e.get("fid") if isinstance(e, dict) else None
        sys.exit(f"fid {fid}: invalid judge/override provenance: {errors}")
    verdict = projected.get("verdict")
    confidence = projected.get("confidence")
    if verdict not in VERDICTS or confidence not in CONFIDENCE:
        sys.exit(f"fid {projected.get('fid')}: invalid original judge call "
                 f"{verdict!r}/{confidence!r}")
    return verdict, confidence


def _sample_population_sha(drafts: list[dict], packets: dict[int, dict]) -> str:
    by_fid = {entry.get("fid"): entry for entry in drafts}
    if set(by_fid) != set(packets):
        missing = sorted(set(packets) - set(by_fid))
        extra = sorted(set(by_fid) - set(packets))
        sys.exit(f"sample population does not match packets; missing={missing}, extra={extra}")
    rows = []
    for fid in sorted(packets):
        verdict, confidence = judge_call(by_fid[fid])
        packet = packets[fid]
        rows.append({"fid": fid, "verdict": verdict, "confidence": confidence,
                     "osm": packet.get("osm"), "prior": packet.get("prior")})
    return hashlib.sha256(json.dumps(rows, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def load_sample_manifest(tmp: str, slug: str, drafts: list[dict]) -> tuple[list[int], str]:
    path = Path(tmp, f"{slug}_keep_sample.json")
    if not path.exists():
        sys.exit(f"sample review requires immutable manifest {path}; rerun judge_review_sheet.py --sample N")
    raw = path.read_bytes()
    value = json.loads(raw)
    if not isinstance(value, dict) or value.get("version") != 1 or value.get("area") != slug:
        sys.exit(f"{path} is not a version-1 sample manifest for {slug}")
    sample = value.get("sample")
    if (not isinstance(sample, list) or any(type(fid) is not int for fid in sample)
            or len(sample) != len(set(sample))):
        sys.exit(f"{path} has invalid sample fids")
    packets_path = Path(tmp, f"{slug}_packets.json")
    if not packets_path.exists():
        sys.exit(f"sample review requires {packets_path}")
    packets = {int(fid): packet for fid, packet in json.loads(packets_path.read_text()).items()}
    population_sha = _sample_population_sha(drafts, packets)
    if value.get("population_sha256") != population_sha:
        sys.exit(f"{path} is not bound to the current original judge calls/packets")
    return sample, hashlib.sha256(raw).hexdigest()


def _runtime_locator_candidates(runtime_directory: Path) -> list[Path]:
    """Enumerate JSON runtime locators through a no-follow directory fd."""
    directory_fd = trusted_fs.open_trusted_directory_fd(runtime_directory)
    try:
        names = os.listdir(directory_fd)
    finally:
        os.close(directory_fd)
    return [
        runtime_directory / name
        for name in sorted(names)
        if isinstance(name, str) and Path(name).name == name
        and name.endswith(".json")
    ]


def load_sample_review_receipt(path: Path, slug: str,
                               drafts: list[dict]) -> tuple[list[int], str]:
    """Validate the canonical frozen review chain and return its sampled fids."""
    import resolve_trust as resolver  # lazy to avoid widening report-only imports

    if not path.is_absolute():
        sys.exit("--review-receipt must be a canonical absolute path")
    try:
        receipt_bytes = trusted_fs.read_regular_bytes(
            path, require_owner_only=True
        )
        receipt = json.loads(receipt_bytes)
        if not isinstance(receipt, dict):
            raise ValueError("review receipt root is not an object")
        if receipt.get("version") == review.REVIEW_RECEIPT_VERSION:
            runtime_directory = path.parent / "runtime"
            candidates = _runtime_locator_candidates(runtime_directory)
            if not candidates:
                raise ValueError("review receipt has no operational source run")
            context = None
            failures = []
            for runtime_path in candidates:
                try:
                    runtime_bytes = trusted_fs.read_regular_bytes(
                        runtime_path, require_owner_only=True
                    )
                    runtime = json.loads(runtime_bytes)
                    required = {
                        "version", "kind", "area", "source_run_id",
                        "source_run_path",
                    }
                    if (not isinstance(runtime, dict) or set(runtime) != required
                            or runtime.get("version") != review.REVIEW_RUNTIME_VERSION
                            or runtime.get("kind") != review.REVIEW_RUNTIME_KIND
                            or runtime.get("area") != slug
                            or runtime.get("source_run_id")
                            != receipt.get("source_run_id")
                            or review.json_bytes(runtime) != runtime_bytes):
                        raise ValueError("review runtime context is invalid")
                    source_run_path = runtime.get("source_run_path")
                    if not isinstance(source_run_path, str):
                        raise ValueError("review runtime source path is invalid")
                    context = resolver.load_review_receipt(
                        path, Path(source_run_path), slug
                    )
                    break
                except (OSError, ValueError, TypeError,
                        json.JSONDecodeError) as error:
                    failures.append(str(error))
            if context is None:
                raise ValueError(
                    "no review runtime locator revalidated the receipt: "
                    + "; ".join(failures)
                )
        else:
            source_run_path = receipt.get("source_run_path")
            if not isinstance(source_run_path, str):
                raise ValueError("review receipt has no operational source run")
            context = resolver.load_review_receipt(
                path, Path(source_run_path), slug
            )
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as error:
        sys.exit(f"sample review receipt is invalid: {error}")
    validated = context["receipt"]
    items = validated["items"]
    by_fid = {entry.get("fid"): entry for entry in drafts}
    if len(by_fid) != len(drafts) or None in by_fid:
        sys.exit("drafts contain duplicate or missing fids")
    if [item["fid"] for item in items] != sorted(by_fid):
        sys.exit("sample review receipt does not cover the current draft population")
    for item in items:
        projected, errors = original_judge_projection(by_fid[item["fid"]])
        if (errors or projected is None
                or tr.decision_projection(projected)
                != tr.decision_projection(item["primary"]["decision"])):
            sys.exit(
                f"sample review receipt fid {item['fid']} does not match the "
                "current original judge call"
            )
    return (
        list(validated["sample"]["selected_fids"]),
        validated["receipt_sha256"],
    )


def record_for(slug: str, drafts: list[dict], reviewed: dict[str, str], spots: list[int],
               note: str | None, date: str, judge: str, sample_fids: list[int] = (),
               sample_manifest_sha256: str | None = None,
               sample_review_receipt_sha256: str | None = None) -> dict:
    by_fid = {entry.get("fid"): entry for entry in drafts}
    if len(by_fid) != len(drafts) or None in by_fid:
        sys.exit("drafts contain duplicate or missing fids")
    if len(sample_fids) != len(set(sample_fids)):
        sys.exit("sample manifest contains duplicate fids")
    sample_set = set(sample_fids)
    unknown = sorted(sample_set - set(by_fid))
    if unknown:
        sys.exit(f"sample manifest names fids not present in the drafts: {unknown}")
    sampled_classes = [verdict for verdict, mode in reviewed.items() if mode == "sample"]
    if len(sampled_classes) > 1:
        sys.exit("one immutable sample manifest can measure only one verdict class")
    if sample_set and not sampled_classes:
        sys.exit("sample fids were supplied but no --reviewed VERDICT=sample class was declared")
    if sampled_classes:
        sampled_class = sampled_classes[0]
        wrong = sorted(fid for fid in sample_set if judge_call(by_fid[fid])[0] != sampled_class)
        if wrong:
            sys.exit(f"sample fids are not original judge {sampled_class} calls: {wrong}")
        if not sample_set:
            sys.exit(f"--reviewed {sampled_class}=sample requires the immutable sample manifest")

    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    sampled: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    flips = []
    human_exceptions = []
    for entry in drafts:
        verdict, confidence = judge_call(entry)
        counts[verdict][confidence] += 1
        if entry.get("fid") in sample_set:
            sampled[verdict][confidence] += 1
        override = entry.get("override")
        if override:
            primary, primary_errors = original_judge_projection(entry)
            machine, machine_errors = machine_decision_projection(entry)
            if primary is None or primary_errors or machine is None or machine_errors:
                sys.exit(f"fid {entry.get('fid')}: invalid layered provenance")
            if primary.get("verdict") != entry.get("verdict"):
                flips.append({"fid": entry.get("fid"), "from": primary.get("verdict"),
                              "to": entry.get("verdict"),
                              "from_confidence": primary.get("confidence"),
                              "by": "human",
                              "sampled": entry.get("fid") in sample_set})
            if entry.get("trust_resolution") and machine.get("verdict") != entry.get("verdict"):
                human_exceptions.append({
                    "fid": entry.get("fid"),
                    "from": machine.get("verdict"),
                    "to": entry.get("verdict"),
                    "by": "human",
                })
    rec = {
        "area": slug, "date": date, "judge": judge, "n": len(drafts),
        "judged": {verdict: dict(sorted(counts[verdict].items()))
                   for verdict in VERDICTS if counts.get(verdict)},
        "reviewed": reviewed,
        "flips": flips,
        "spot_checks": sorted(spots),
        "note": note,
    }
    if human_exceptions:
        rec["human_exceptions"] = human_exceptions
    if sample_fids:
        rec["sample"] = {verdict: dict(sorted(sampled[verdict].items()))
                         for verdict in VERDICTS if sampled.get(verdict)}
        rec["sample_fids"] = sorted(sample_set)
        if sample_review_receipt_sha256 is not None:
            rec["sample_review_receipt_sha256"] = sample_review_receipt_sha256
        elif sample_manifest_sha256 is not None:
            rec["sample_manifest_sha256"] = sample_manifest_sha256
    return rec


def load_ledger() -> dict:
    if os.path.exists(LEDGER):
        return json.load(open(LEDGER))
    return {"about": ("Human-vs-judge agreement per area for the parking vision "
                      "adjudication. Written by tools/calibration.py; read by its "
                      "report. `reviewed` says which verdict classes the human looked "
                      "at one lot at a time (each), accepted without per-lot review "
                      "(en-bloc) or did not look at (none); only `each` counts toward "
                      "agreement. Flips are the human's overrides of the judge."),
            "areas": []}


def wilson_upper(k: int, n: int, z: float = Z95) -> float:
    """Upper end of the Wilson score interval for a proportion k/n. With k=0 it
    is close to the rule of three (3/n) for large n."""
    if n <= 0:
        return 1.0
    p = k / n
    denom = 1 + z * z / n
    centre = p + z * z / (2 * n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return min(1.0, (centre + half) / denom)


def n_for_target(target: float, z: float = Z95) -> int:
    """Smallest n such that zero flips in n reviews gives wilson_upper <= target."""
    n = 1
    while wilson_upper(0, n, z) > target:
        n += 1
        if n > 100000:
            break
    return n


def report(ledger: dict, target: float, min_n: int) -> str:
    stats: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"judged": 0, "reviewed": 0, "flips": 0})
    per_area = []
    resolved: dict[str, int] = defaultdict(int)          # REVIEW -> human's call
    out_of_sample: dict[tuple[str, str], int] = defaultdict(int)
    for r in ledger.get("areas", []):
        all_flips_by = defaultdict(int)
        sampled_flips_by = defaultdict(int)
        for flip in r.get("flips", []):
            if flip["from"] == "REVIEW":
                # A REVIEW is the judge deferring, not calling; the human's
                # resolution is counted, never scored as a miss.
                resolved[flip["to"]] += 1
                continue
            key = (flip["from"], flip.get("from_confidence") or "unrated")
            all_flips_by[key] += 1
            if flip.get("sampled"):
                sampled_flips_by[key] += 1
            elif (r.get("reviewed") or {}).get(flip["from"], "none") == "sample":
                out_of_sample[key] += 1
        n_rev = 0
        for verdict, by_confidence in r.get("judged", {}).items():
            mode = (r.get("reviewed") or {}).get(verdict, "none")
            for confidence, judged_count in by_confidence.items():
                stat = stats[(verdict, confidence)]
                stat["judged"] += judged_count
                if mode == "each":
                    stat["reviewed"] += judged_count
                    stat["flips"] += all_flips_by.get((verdict, confidence), 0)
                    n_rev += judged_count
                elif mode == "sample":
                    reviewed_count = (r.get("sample") or {}).get(verdict, {}).get(confidence, 0)
                    stat["reviewed"] += reviewed_count
                    stat["flips"] += sampled_flips_by.get((verdict, confidence), 0)
                    n_rev += reviewed_count
                else:
                    # Preserve evidence of a miss even without a denominator;
                    # report() labels this class unmeasured rather than deriving
                    # an agreement percentage.
                    stat["flips"] += all_flips_by.get((verdict, confidence), 0)
        per_area.append((r["area"], r["date"], r["n"], n_rev, len(r.get("flips", []))))

    lines = ["=== calibration: human vs judge ===",
             f"{'area':42} {'date':10} {'judged':>6} {'reviewed':>8} {'flips':>5}"]
    for a, d, n, nr, nf in per_area:
        lines.append(f"{a:42} {d:10} {n:6} {nr:8} {nf:5}")
    lines += ["", f"{'judge call':22} {'judged':>6} {'reviewed':>8} {'flips':>5} {'agree':>7} "
                  f"{'miss<=95%':>9}  status (target {target:.0%}, min n {min_n})"]
    # One aggregate row per verdict ("DROP any") ahead of its confidence rows:
    # the per-confidence cells are thin early on and would hide the headline.
    for v in ("KEEP", "DROP"):
        cells = [s for (vv, _), s in stats.items() if vv == v]
        if cells:
            stats[(v, "any")] = {k: sum(s[k] for s in cells) for k in ("judged", "reviewed", "flips")}
    order = [(v, c) for v in ("KEEP", "DROP") for c in ("any",) + CONFIDENCE + ("unrated",)]
    seen = {k for k in stats if k[0] != "REVIEW"}
    for key in order + sorted(seen - set(order)):
        if key not in stats:
            continue
        s = stats[key]
        v, c = key
        n, k = s["reviewed"], s["flips"]
        if n == 0:
            agree = ub = "-"
            status = (f"{k} flip(s) caught without a per-lot pass; class not measured" if k
                      else "not measured (accepted en bloc or unreviewed)")
        elif k > n:
            agree = ub = "-"
            status = f"INVALID LEDGER: {k} measured flips exceed {n} reviewed"
        else:
            agree = f"{(n - k) / n:.1%}"
            u = wilson_upper(k, n)
            ub = f"{u:.1%}"
            if n >= min_n and u <= target:
                status = "AUTO-OK"
            elif k == 0:
                need = max(n_for_target(target), min_n) - n
                status = f"needs {need} more zero-flip reviews" if need > 0 else "AUTO-OK"
            else:
                status = f"{k} flip(s); keep reviewing"
        lines.append(f"{v + ' ' + c:22} {s['judged']:6} {n:8} {k:5} {agree:>7} {ub:>9}  {status}")
    if out_of_sample:
        detail = ", ".join(f"{verdict}/{confidence}={count}"
                           for (verdict, confidence), count in sorted(out_of_sample.items()))
        lines.append(f"out-of-sample flips (reported, excluded from sampled agreement): {detail}")
    n_review = sum(s["judged"] for (vv, _), s in stats.items() if vv == "REVIEW")
    if n_review:
        res = ", ".join(f"{n} -> {to}" for to, n in sorted(resolved.items())) or "none resolved yet"
        lines.append(f"{'REVIEW (deferred)':22} {n_review:6}  resolved by the human: {res}"
                     f"{'  (every REVIEW became a DROP: the judge could have called it, lesson 10)' if resolved and set(resolved) == {'DROP'} and sum(resolved.values()) == n_review else ''}")
    lines += ["", f"(zero flips in n reviews bounds the miss rate at about 3/n; "
                  f"{n_for_target(target)} reviews reach {target:.0%}, "
                  f"{n_for_target(0.01)} reach 1%)"]
    return "\n".join(lines)


def _add_command(args) -> dict:
    reviewed = {}
    for value in args.reviewed:
        verdict, _, mode = value.partition("=")
        if verdict not in VERDICTS or mode not in REVIEW_MODES:
            sys.exit(f"--reviewed wants VERDICT=each|sample|en-bloc|none, got {value!r}")
        reviewed[verdict] = mode
    resource_lock = tr.resource_lock_path(PADJ_TMP, LEDGER)
    area_lock = tr.area_lock_path(PADJ_TMP, args.slug)
    # Global resource first, then area: every writer uses this order.
    with trusted_fs.open_lock_file(resource_lock) as resource_handle:
        fcntl.flock(resource_handle.fileno(), fcntl.LOCK_EX)
        with trusted_fs.open_lock_file(area_lock) as area_handle:
            fcntl.flock(area_handle.fileno(), fcntl.LOCK_EX)
            if tr.authority_journal_path(PADJ_TMP, args.slug).exists():
                sys.exit(
                    "RECOVERY_REQUIRED: live human-authority journal blocks "
                    "calibration writes"
                )
            ledger = load_ledger()
            drafts = load_drafts(PADJ_TMP, args.slug)
            declared_sample = [verdict for verdict, mode in reviewed.items() if mode == "sample"]
            typed_sample = [int(x) for x in args.sample_fids.split(",") if x.strip()]
            sample_fids: list[int] = []
            sample_manifest_sha = None
            sample_review_receipt_sha = None
            if declared_sample:
                if len(declared_sample) != 1 or declared_sample[0] != "KEEP":
                    sys.exit("canonical review receipts can sample only original judge KEEP calls")
                if args.legacy_sample_manifest:
                    if args.review_receipt is not None:
                        sys.exit(
                            "--legacy-sample-manifest and --review-receipt are mutually exclusive"
                        )
                    sample_fids, sample_manifest_sha = load_sample_manifest(
                        PADJ_TMP, args.slug, drafts
                    )
                else:
                    if args.review_receipt is None:
                        sys.exit(
                            "sample review requires --review-receipt PATH; use "
                            "--legacy-sample-manifest only for explicit legacy replay"
                        )
                    sample_fids, sample_review_receipt_sha = (
                        load_sample_review_receipt(
                            args.review_receipt, args.slug, drafts
                        )
                    )
                if typed_sample and typed_sample != sample_fids:
                    sys.exit(f"--sample-fids must exactly match the review receipt: {sample_fids}")
            elif typed_sample:
                sys.exit("--sample-fids requires --reviewed VERDICT=sample")
            elif args.review_receipt is not None or args.legacy_sample_manifest:
                sys.exit(
                    "--review-receipt/--legacy-sample-manifest requires "
                    "--reviewed KEEP=sample"
                )
            rec = record_for(
                args.slug, drafts, reviewed, args.spot, args.note, args.date,
                args.judge, sample_fids, sample_manifest_sha,
                sample_review_receipt_sha,
            )
            ledger["areas"] = [x for x in ledger["areas"] if x["area"] != args.slug] + [rec]
            ledger_bytes = json.dumps(
                ledger, indent=1, ensure_ascii=False
            ).encode("utf-8")
            trusted_fs.atomic_write_bytes(Path(LEDGER), ledger_bytes)
    print(f"recorded {args.slug}: {rec['n']} judged, {rec['judged']}, "
          f"{len(rec['flips'])} flip(s), reviewed {reviewed}")
    print(f"wrote {LEDGER}")
    return ledger


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add", help="append (or replace) one area's record from its drafts")
    a.add_argument("slug")
    a.add_argument("--reviewed", action="append", default=[],
                   metavar="VERDICT=each|sample|en-bloc|none",
                   help="how the human reviewed each verdict class (default none)")
    a.add_argument("--spot", type=int, action="append", default=[], metavar="FID",
                   help="fids the integrator spot-read independently of the human")
    a.add_argument("--sample-fids", default="", metavar="FID,FID,...",
                   help="optional assertion matching the receipt-derived sample")
    a.add_argument("--review-receipt", type=Path,
                   help="canonical frozen review receipt for sample calibration")
    a.add_argument("--legacy-sample-manifest", action="store_true",
                   help="explicitly replay the obsolete <slug>_keep_sample.json path")
    a.add_argument("--note")
    a.add_argument("--date", default=_dt.date.today().isoformat())
    a.add_argument("--judge", default="agent-fanout")
    r = sub.add_parser("report", help="agreement per judge call with 95%% miss-rate bounds")
    r.add_argument("--target", type=float, default=0.02)
    r.add_argument("--min-n", type=int, default=100)
    args = ap.parse_args(argv)

    if args.cmd == "add":
        ledger = _add_command(args)
    else:
        ledger = load_ledger()
    print(report(ledger, getattr(args, "target", 0.02), getattr(args, "min_n", 100)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
