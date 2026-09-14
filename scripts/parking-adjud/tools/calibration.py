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
        --reviewed DROP=each --reviewed REVIEW=each --reviewed KEEP=en-bloc \
        [--spot FID ...] [--note TEXT] [--date YYYY-MM-DD]
    python3 tools/calibration.py report [--target 0.02] [--min-n 100]

`add` reads the area's verdict drafts (after the overrides are applied) and
appends one record to `data/calibration.json`; the judge is scored on its
ORIGINAL call (an overridden entry counts under `override.from`). A class the
human only accepted en bloc is recorded but not counted as reviewed: silence
is not agreement. Re-adding an area replaces its record.

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
import glob
import json
import math
import os
import sys
from collections import defaultdict
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
PADJ_TMP = os.environ.get("PADJ_TMP") or os.path.join(_HERE, "..", "work")
LEDGER = os.environ.get("PADJ_CALIBRATION") or os.path.join(_HERE, "..", "data", "calibration.json")
VERDICTS = ("KEEP", "DROP", "REVIEW")
CONFIDENCE = ("certain", "strong", "leaning")
REVIEW_MODES = ("each", "en-bloc", "none")
Z95 = 1.959964


def load_drafts(tmp: str, slug: str) -> list[dict]:
    single = Path(tmp) / f"{slug}_verdict_draft.json"
    files = [single] if single.exists() else sorted(
        Path(p) for p in glob.glob(str(Path(tmp) / f"{slug}_verdict_draft_*.json")))
    if not files:
        sys.exit(f"no verdict drafts for {slug} under {tmp}")
    out: list[dict] = []
    for f in files:
        out.extend(json.load(open(f)))
    return out


def judge_call(e: dict) -> tuple[str, str]:
    """(verdict, confidence) as the JUDGE made it, before any human flip."""
    ov = e.get("override")
    if ov and ov.get("from"):
        return ov["from"], ov.get("confidence_from") or e.get("confidence") or "unrated"
    return e.get("verdict"), e.get("confidence") or "unrated"


def record_for(slug: str, drafts: list[dict], reviewed: dict[str, str], spots: list[int],
               note: str | None, date: str, judge: str) -> dict:
    counts: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    flips = []
    for e in drafts:
        v, c = judge_call(e)
        counts[v][c] += 1
        ov = e.get("override")
        if ov and ov.get("from") and ov["from"] != e.get("verdict"):
            flips.append({"fid": e.get("fid"), "from": ov["from"], "to": e.get("verdict"),
                          "from_confidence": ov.get("confidence_from"), "by": ov.get("by", "human")})
    return {
        "area": slug, "date": date, "judge": judge, "n": len(drafts),
        "judged": {v: dict(sorted(counts[v].items())) for v in VERDICTS if counts.get(v)},
        "reviewed": reviewed,
        "flips": flips,
        "spot_checks": sorted(spots),
        "note": note,
    }


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
    for r in ledger.get("areas", []):
        flips_by = defaultdict(int)
        for f in r.get("flips", []):
            if f["from"] == "REVIEW":
                # A REVIEW is the judge deferring, not calling; the human's
                # resolution is counted, never scored as a miss.
                resolved[f["to"]] += 1
                continue
            flips_by[(f["from"], f.get("from_confidence") or "unrated")] += 1
        n_rev = 0
        for v, by_conf in r.get("judged", {}).items():
            mode = (r.get("reviewed") or {}).get(v, "none")
            for c, n in by_conf.items():
                s = stats[(v, c)]
                s["judged"] += n
                # A flip is evidence of a miss whether or not the class was
                # systematically reviewed; only the denominator needs `each`.
                s["flips"] += flips_by.get((v, c), 0)
                if mode == "each":
                    s["reviewed"] += n
                    n_rev += n
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
    a.add_argument("--reviewed", action="append", default=[], metavar="VERDICT=each|en-bloc|none",
                   help="how the human reviewed each verdict class (default none)")
    a.add_argument("--spot", type=int, action="append", default=[], metavar="FID",
                   help="fids the integrator spot-read independently of the human")
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
                sys.exit(f"--reviewed wants VERDICT=each|en-bloc|none, got {s!r}")
            reviewed[v] = mode
        drafts = load_drafts(PADJ_TMP, args.slug)
        rec = record_for(args.slug, drafts, reviewed, args.spot, args.note, args.date, args.judge)
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
