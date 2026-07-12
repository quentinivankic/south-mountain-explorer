#!/usr/bin/env python3
"""Report seeded areas that got auto-excluded for showing an actual RED FLAG
for private/restricted land — a lightweight after-the-fact log, not a gate.

`red_flag()` (in _seed_constants.py, shared with is_quality()) is now called
DIRECTLY by the seeding pipeline — a flagged area never gets seeded in the
first place, no manual review-then-strip step needed. This script exists so
you still see WHAT got excluded and WHY, without having to approve it first.

History: v1 tried to REQUIRE positive proof of public ownership (a
government tag, a recognized land-trust name, a "State Forest"-style
designation) before considering an area safe. Real NY data showed that
doesn't scale: it flagged 439 areas for review, and sampling them showed
the overwhelming majority were legitimate — `Sands Point Preserve`
(operator: County of Nassau), `Spring Creek Park` (National Park Service),
a dozen `North Shore Land Alliance` preserves — all correctly public, just
using operator names or org-naming conventions (County of X, Town of X,
"Alliance", "Foundation", "Heritage Trust", ...) that no fixed keyword list
can ever fully enumerate. v2/v3 flipped to a narrow, evidence-backed set of
actual red flags instead (mine/mining/quarry, hunting club/preserve, water
supply/watershed — see _seed_constants.red_flag()'s docstring for the full
story). Verified against 17 real cases with zero false positives, plus clean
0-flag runs on GA and VT, before being promoted from "flag for review" to an
automatic exclusion.

Usage:
    python3 scripts/audit-easement-ownership.py NY
    python3 scripts/audit-easement-ownership.py NY --out ny-review.txt
    python3 scripts/audit-easement-ownership.py NY VT GA CO   # multiple states, one combined file
    python3 scripts/audit-easement-ownership.py --all --out all-states-review.txt

Multi-state mode retries each state's Overpass query up to 3x (60/180/600s
backoff, same as seed-areas.py) before giving up on it and moving on — one
flaky state (Overpass timeouts are common on big/dense states) doesn't block
the batch. Failed states print at the end so you can re-run just those.

Run on the homelab (needs live Overpass). Read-only — writes nothing to
index.json.
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
import time
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS_DIR))
from _seed_constants import is_quality, red_flag, STATE_NAMES  # noqa: E402

# seed-areas.py has a hyphen, so it can't be a plain `import` target.
_spec = importlib.util.spec_from_file_location(
    "seed_areas", _SCRIPTS_DIR / "seed-areas.py")
_seed_areas = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_seed_areas)
overpass_query = _seed_areas.overpass_query
fetch_overpass = _seed_areas.fetch_overpass

# Same backoff as seed-areas.py's per-state retry, so a transient Overpass
# 504/timeout doesn't require a manual re-run of the whole batch.
_RETRY_BACKOFFS_SECONDS = [60, 180, 600]


def audit_state(state: str) -> tuple[int, int, list[tuple[dict, str]]]:
    """(safe_count, dropped_count, review_list) for one state. Retries a
    flaky Overpass response up to 3x (60/180/600s) before raising. `review`
    here means "auto-excluded by red_flag()", reported after the fact —
    `dropped` is every OTHER is_quality() exclusion (access/ownership=
    private, no protect_class/keyword match)."""
    last_err: Exception | None = None
    data = None
    for attempt, backoff in enumerate(_RETRY_BACKOFFS_SECONDS, start=1):
        try:
            data = fetch_overpass(overpass_query(state))
            break
        except RuntimeError as e:
            last_err = e
            if attempt == len(_RETRY_BACKOFFS_SECONDS):
                raise
            print(f"  {state}: attempt {attempt} failed ({e}); "
                  f"sleeping {backoff}s before retry", file=sys.stderr)
            time.sleep(backoff)
    assert data is not None, f"unreachable: fetch succeeded but data is None ({last_err})"

    review: list[tuple[dict, str]] = []
    safe = dropped = 0
    for el in data.get("elements", []):
        if el.get("type") not in ("relation", "way"):
            continue
        tags = el.get("tags") or {}
        flag = red_flag(tags)
        if flag is not None:
            review.append((tags, flag))
            continue
        if is_quality(tags):
            safe += 1
        else:
            dropped += 1
    return safe, dropped, review


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("states", nargs="*", help="2-letter state codes, e.g. NY VT GA")
    ap.add_argument("--all", action="store_true", help="audit every region in STATE_NAMES")
    ap.add_argument("--out", help="write the combined review list to this file")
    args = ap.parse_args(argv)

    if args.all:
        if args.states:
            ap.error("--all is mutually exclusive with positional state codes")
        states = sorted(STATE_NAMES.keys())
    elif args.states:
        states = args.states
    else:
        ap.error("at least one state code is required (or pass --all)")

    all_lines: list[str] = []
    totals = {"safe": 0, "dropped": 0, "review": 0}
    failed_states: list[str] = []

    for state in states:
        print(f"Querying Overpass for {state}...", file=sys.stderr)
        try:
            safe, dropped, review = audit_state(state)
        except RuntimeError as e:
            print(f"  {state}: giving up after 3 attempts ({e})", file=sys.stderr)
            failed_states.append(state)
            continue

        totals["safe"] += safe
        totals["dropped"] += dropped
        totals["review"] += len(review)
        print(f"\n{state}: {safe} seeded, {dropped} excluded (access/ownership="
              f"private or no protect_class/keyword match), {len(review)} "
              f"auto-excluded (red flag)\n")

        for tags, flag in sorted(review, key=lambda t: t[0].get("name") or ""):
            name = tags.get("name") or "(unnamed)"
            operator = tags.get("operator") or "(no operator tag)"
            desc = tags.get("description") or ""
            line = f"  [{state}] [{flag}]  {name!r}  operator={operator!r}"
            if desc:
                line += f"  desc={desc!r}"
            all_lines.append(line)
            print(line)

    print(f"\n=== TOTAL across {len(states) - len(failed_states)} state(s): "
          f"{totals['safe']} seeded, {totals['dropped']} excluded, "
          f"{totals['review']} auto-excluded (red flag) ===")
    if failed_states:
        print(f"FAILED (Overpass gave up after 3 attempts, re-run these "
              f"separately): {' '.join(failed_states)}")

    if args.out:
        Path(args.out).write_text("\n".join(all_lines) + "\n")
        print(f"\nWrote {len(all_lines)} lines to {args.out} — this is a report "
              f"of what was auto-excluded, not something to act on", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
