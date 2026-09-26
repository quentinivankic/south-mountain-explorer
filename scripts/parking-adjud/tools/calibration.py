#!/usr/bin/env python3
"""Calibration ledger: how often does the human agree with the judge?

The judge fan-out produces verdicts; a human eyeballs some of them and flips a
few (`merge_drafts.py --set`). This ledger records, per area, what the judge
said (by verdict and confidence), which verdict classes the human reviewed one
lot at a time versus accepted en bloc, and every flip. The report turns that
into agreement rates with a 95% upper bound on the miss rate, per class, so
the question "is the judge good enough to stop reviewing every DROP?" gets a
number instead of a feeling.

    PADJ_TMP=work/co python3 tools/calibration.py add <slug> \
        --reviewed DROP=each --reviewed REVIEW=each --reviewed KEEP=sample \
        --sample-fids 3,17,42 [--spot FID ...] [--note TEXT] [--date YYYY-MM-DD]
    python3 tools/calibration.py report [--target 0.02] [--min-n 100]

`add` reads the area's verdict drafts (after the overrides are applied) and
appends one record to `data/calibration.json`; the judge is scored on its
ORIGINAL call (an overridden entry counts under `override.from`). A class the
human only accepted en bloc is recorded but not counted as reviewed: silence
is not agreement. `--reviewed KEEP=sample --sample-fids 3,17,...` says the
human went through exactly those lots one by one (the sheet's SAMPLE cards);
only they enter the KEEP denominator. Re-adding an area replaces its record.

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
from judge_validation import canonical_draft_files, original_judge_projection  # noqa: E402

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
    if (not isinstance(sample, list) or any(not isinstance(fid, int) for fid in sample)
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


def record_for(slug: str, drafts: list[dict], reviewed: dict[str, str], spots: list[int],
               note: str | None, date: str, judge: str, sample_fids: list[int] = (),
               sample_manifest_sha256: str | None = None) -> dict:
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
    for entry in drafts:
        verdict, confidence = judge_call(entry)
        counts[verdict][confidence] += 1
        if entry.get("fid") in sample_set:
            sampled[verdict][confidence] += 1
        override = entry.get("override")
        if override and override.get("from") and override["from"] != entry.get("verdict"):
            flips.append({"fid": entry.get("fid"), "from": override["from"],
                          "to": entry.get("verdict"),
                          "from_confidence": override.get("confidence_from"),
                          "by": override.get("by", "human"),
                          "sampled": entry.get("fid") in sample_set})
    rec = {
        "area": slug, "date": date, "judge": judge, "n": len(drafts),
        "judged": {verdict: dict(sorted(counts[verdict].items()))
                   for verdict in VERDICTS if counts.get(verdict)},
        "reviewed": reviewed,
        "flips": flips,
        "spot_checks": sorted(spots),
        "note": note,
    }
    if sample_fids:
        rec["sample"] = {verdict: dict(sorted(sampled[verdict].items()))
                         for verdict in VERDICTS if sampled.get(verdict)}
        rec["sample_fids"] = sorted(sample_set)
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
                   help="the lots the human reviewed one by one inside a `sample` class "
                        "(judge_review_sheet.py --sample draws and prints them)")
    a.add_argument("--note")
    a.add_argument("--date", default=_dt.date.today().isoformat())
    a.add_argument("--judge", default="agent-fanout")
    r = sub.add_parser("report", help="agreement per judge call with 95%% miss-rate bounds")
    r.add_argument("--target", type=float, default=0.02)
    r.add_argument("--min-n", type=int, default=100)
    args = ap.parse_args(argv)

    ledger = load_ledger()
    if args.cmd == "add":
        reviewed = {}
        for s in args.reviewed:
            v, _, mode = s.partition("=")
            if v not in VERDICTS or mode not in REVIEW_MODES:
                sys.exit(f"--reviewed wants VERDICT=each|sample|en-bloc|none, got {s!r}")
            reviewed[v] = mode
        drafts = load_drafts(PADJ_TMP, args.slug)
        declared_sample = [verdict for verdict, mode in reviewed.items() if mode == "sample"]
        typed_sample = [int(x) for x in args.sample_fids.split(",") if x.strip()]
        sample_fids: list[int] = []
        sample_manifest_sha = None
        if declared_sample:
            sample_fids, sample_manifest_sha = load_sample_manifest(PADJ_TMP, args.slug, drafts)
            if typed_sample and typed_sample != sample_fids:
                sys.exit(f"--sample-fids must exactly match the immutable manifest: {sample_fids}")
        elif typed_sample:
            sys.exit("--sample-fids requires --reviewed VERDICT=sample")
        rec = record_for(args.slug, drafts, reviewed, args.spot, args.note, args.date,
                         args.judge, sample_fids, sample_manifest_sha)
        ledger["areas"] = [x for x in ledger["areas"] if x["area"] != args.slug] + [rec]
        os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
        json.dump(ledger, open(LEDGER, "w"), indent=1, ensure_ascii=False)
        print(f"recorded {args.slug}: {rec['n']} judged, {rec['judged']}, "
              f"{len(rec['flips'])} flip(s), reviewed {reviewed}")
        print(f"wrote {LEDGER}")
    print(report(ledger, getattr(args, "target", 0.02), getattr(args, "min_n", 100)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
