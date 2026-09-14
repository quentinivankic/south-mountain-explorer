#!/usr/bin/env python3
"""Validate judge verdict drafts for one or more areas and fold them into an
OSM-id-keyed verdict store. The general form of `merge_ne.py`.

    python3 merge_drafts.py <store.json> <slug> [<slug> ...] [--write]
                            [--set FID=VERDICT ...] [--note TEXT] [--judged DATE]

For each area it reads `<slug>_verdict_draft.json` (a LIST of verdict objects
in the `judge_protocol.md` schema — chunk drafts `<slug>_verdict_draft_NN.json`
are concatenated first if the single file is absent), checks it against
`<slug>_pub.txt` (every judge fid must be present) and the schema, prints every
DROP with its evidence and tile path so a human can eyeball each one, flags
DROPs of a surveyed prior (canopy must never be the reason), and only with
`--write` folds the verdicts into the store.

`--set FID=VERDICT` is the human's pen (one area at a time): it overrides the
judge's verdict for that lot, records the flip as `override` ({from, by, date,
note, resolve_hint, confidence_from}) with confidence `strong`, and writes the
change back into the draft file it came from (atomically, with or without
`--write`), so the drafts, the store and the calibration ledger all agree.
`override.from` is always the JUDGE's call: a second `--set` on the same lot
revises the override but keeps it, and setting the lot back to the judge's
call removes the override. Setting a lot to what it already is does nothing.
Every store record carries `judged` (the merge date, or `--judged YYYY-MM-DD`),
because a store accumulates areas across many days; a lot already held by
another area is refused when the verdicts disagree and left alone when they
agree.

The store entry is SELF-CONTAINED: besides the verdict it carries `area`, the
lot's `lat`/`lon`, `rings` (mapped footprint) and `name` from the dossier, so
`scripts/build-parking-verdicts.py` can place it without the dossier being in
the repo. The Arizona / New England / Zion / Griffith stores predate this and
still resolve through their committed dossiers; new batches commit only the
store.
"""
from __future__ import annotations

import datetime as _dt
import glob
import json
import os
import sys
from pathlib import Path

_HERE = os.path.dirname(os.path.abspath(__file__))
PADJ_TMP = os.environ.get("PADJ_TMP") or os.path.join(_HERE, "..", "work")
REQ = ["fid", "osm", "prior", "verdict", "exists", "public", "serves", "confidence"]
VERDICTS = ("KEEP", "DROP", "REVIEW")
CONFIDENCE = ("certain", "strong", "leaning")


def draft_files(tmp: Path, slug: str) -> list[Path]:
    single = tmp / f"{slug}_verdict_draft.json"
    if single.exists():
        return [single]
    return [Path(p) for p in sorted(glob.glob(str(tmp / f"{slug}_verdict_draft_*.json")))]


def load_draft(tmp: Path, slug: str) -> list[dict] | None:
    files = draft_files(tmp, slug)
    if not files:
        return None
    out: list[dict] = []
    for p in files:
        out.extend(json.load(open(p)))
    return out


def _dump_atomic(path: Path, rows: list[dict]) -> None:
    """The drafts are the only copy of the judges' work; never leave one half-written."""
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    with open(tmp_path, "w") as fh:
        json.dump(rows, fh, indent=1, ensure_ascii=False)
    os.replace(tmp_path, path)


def set_verdict(e: dict, to: str, note: str | None, today: str) -> str:
    """Apply one human decision to one draft entry, in place, and say what
    happened. The judge's ORIGINAL call is what `override.from` records, so a
    second flip keeps it, and flipping back to the judge's call removes the
    override altogether (nothing was overridden in the end)."""
    fid, was = e.get("fid"), e.get("verdict")
    ov = e.get("override")
    if was == to:
        return f"  #{fid}: already {to}, nothing to set"
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
                    today: str) -> list[str]:
    """Flip the judge's verdict for each fid in `sets`, in the draft file that
    holds it, recording the flip. Returns human-readable lines; a fid that is
    not in any draft is reported, not invented."""
    lines: list[str] = []
    pending = dict(sets)
    for path in draft_files(tmp, slug):
        rows = json.load(open(path))
        changed = False
        for e in rows:
            fid = e.get("fid")
            if fid not in pending:
                continue
            before = json.dumps(e, sort_keys=True)
            lines.append(set_verdict(e, pending.pop(fid), note, today) + f" [{path.name}]")
            changed = changed or json.dumps(e, sort_keys=True) != before
        if changed:
            _dump_atomic(path, rows)
    for fid, to in pending.items():
        lines.append(f"  !! #{fid}: not in any {slug} draft, cannot set {to}")
    return lines


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


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    write = "--write" in argv
    argv = [a for a in argv if a != "--write"]
    sets_raw = _take_opt(argv, "--set", repeat=True)
    note = _take_opt(argv, "--note")
    today = _take_opt(argv, "--judged") or _dt.date.today().isoformat()
    try:
        _dt.date.fromisoformat(today)
    except ValueError:
        sys.exit(f"--judged wants YYYY-MM-DD, got {today!r}")
    if len(argv) < 2:
        print(__doc__, file=sys.stderr)
        return 2
    store_path, slugs = Path(argv[0]), argv[1:]
    tmp = Path(PADJ_TMP)
    store = json.load(open(store_path)) if store_path.exists() else {}

    issues: list[str] = []
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
        for line in apply_overrides(tmp, slugs[0], sets, note, today):
            print(line)
            if line.lstrip().startswith("!!"):
                issues.append(line.strip())
    drops: list[tuple[str, dict, str]] = []
    entries: list[tuple[str, dict, dict]] = []      # (slug, verdict, facility)
    totals = {v: 0 for v in VERDICTS}
    for slug in slugs:
        arr = load_draft(tmp, slug)
        if arr is None:
            issues.append(f"MISSING draft: {slug}")
            continue
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
            for k in REQ:
                if k not in e:
                    issues.append(f"{slug} fid{fid}: missing '{k}'")
            v = e.get("verdict")
            if v not in VERDICTS:
                issues.append(f"{slug} fid{fid}: bad verdict {v!r}")
                continue
            if e.get("confidence") not in CONFIDENCE:
                issues.append(f"{slug} fid{fid}: bad confidence {e.get('confidence')!r}")
            if v == "REVIEW" and not e.get("resolve_hint"):
                issues.append(f"{slug} fid{fid}: REVIEW without resolve_hint")
            fac = fac_by_fid.get(fid)
            if fac is None:
                issues.append(f"{slug} fid{fid}: not in the dossier")
                continue
            if fid not in pub:
                issues.append(f"{slug} fid{fid}: not in the judge set (_pub.txt)")
            if not e.get("osm"):
                e["osm"] = list(fac.get("osm") or [])
            if not e.get("osm"):
                issues.append(f"{slug} fid{fid}: no OSM id")
            for ax in ("exists", "public", "serves"):
                a = e.get(ax) or {}
                if a.get("call") not in ("yes", "no", "unclear", "n/a"):
                    issues.append(f"{slug} fid{fid}: {ax}.call is {a.get('call')!r}")
                if not a.get("evidence"):
                    issues.append(f"{slug} fid{fid}: {ax} has no evidence")
            if v == "DROP" and e.get("prior") == "surveyed" and "z3" not in [
                    z.lower() for z in e.get("frames_used") or []]:
                issues.append(f"{slug} fid{fid}: DROP of a surveyed prior without Z3 in frames_used")
            # Areas overlap; a lot judged under another area must not be
            # silently re-decided here. `judge_packets.py --skip-judged` keeps
            # such lots out of the judge set upstream — this is the backstop.
            held = next((store[o] for o in e.get("osm") or [] if o in store
                         and store[o].get("area") not in (None, slug)), None)
            if held is not None and held.get("verdict") != v:
                issues.append(f"{slug} fid{fid}: {v} but {held.get('area')} already holds "
                              f"{held.get('osm', ['?'])[0]} as {held.get('verdict')} — resolve, do not overwrite")
            counts[v] += 1
            name = (fac.get("tags_union") or {}).get("name") or "(unnamed)"
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
        return 1 if issues else 0
    if issues:
        print("\n!! refusing to --write with issues outstanding", file=sys.stderr)
        return 1

    before = len(store)
    kept = 0
    for slug, e, fac in entries:
        rec = dict(e)
        rec["area"] = slug
        rec.setdefault("src", "judge-fanout")
        # A re-merge of an already-stored area keeps its first merge date, so
        # re-running the fold is not a diff.
        prev = next((store[o] for o in (e.get("osm") or []) if o in store), None)
        if prev is not None and prev.get("area") not in (None, slug):
            # Same lot, same verdict (the check above refused otherwise), first
            # area's record stands.
            kept += 1
            continue
        rec.setdefault("judged", (prev or {}).get("judged") or today)
        rec["lat"] = round(float(fac["lat"]), 6)
        rec["lon"] = round(float(fac["lon"]), 6)
        rings = fac.get("rings") or ([fac["ring"]] if fac.get("ring") else [])
        rec["rings"] = [[[round(float(p[0]), 6), round(float(p[1]), 6)] for p in ring]
                        for ring in rings if ring]
        rec["name"] = (fac.get("tags_union") or {}).get("name")
        # The store's ids first, then the rest of the cluster.
        ids = list(rec.get("osm") or [])
        ids += [o for o in fac.get("osm") or [] if o not in ids]
        rec["osm"] = ids
        for oid in ids:
            store[oid] = rec
    json.dump(store, open(store_path, "w"), indent=0, ensure_ascii=False)
    print(f"\nWROTE {store_path}: {before} -> {len(store)} osm keys "
          f"({len(entries) - kept} verdicts merged"
          + (f", {kept} already held by another area, kept)" if kept else ")"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
