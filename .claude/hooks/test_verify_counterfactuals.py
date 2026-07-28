#!/usr/bin/env python3
"""Regression tests for the Stop-hook guard.

The fixtures are not invented. Cases 1 and 2 are the VERBATIM messages from
2026-07-28 that produced the rule: the assertion the user challenged, and the
correction that followed after actually running the check. If the guard cannot
separate those two, it does not do its job.

    python3 .claude/hooks/test_verify_counterfactuals.py
"""
import importlib.util
import os
import sys

_spec = importlib.util.spec_from_file_location(
    "guard", os.path.join(os.path.dirname(os.path.abspath(__file__)),
                          "verify-counterfactuals.py"))
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

# The real sentence that started this. Every "evidence marker" a naive detector
# would look for is present — two backticked paths, a PR number, an identifier —
# and the claim was still false. This is the case that rules out evidence
# detection as a design.
BAD_VELOCITY = """**The cheapest fix available, and it's concrete: CI runs no Python tests at all.**

There are 9 test files across `scripts/` and `trailforge/`. All pass. Nothing in
`.github/workflows/` invokes any of them.

A two-minute job on `scripts/**` and `trailforge/**` would have caught #425's
breakage the day `road_gate` changed shape."""

# The correction, after grafting the function onto main and running the suite.
GOOD_CORRECTION = """**No. I tested my own claim and it was wrong.**

I said a CI job running the existing tests would have caught #425. So I grafted
#425's function onto current `main`, added its own test, and ran the suite.

**31 passed.** CI would have sailed straight past it."""

# Ordinary reporting with no causal claim at all.
NEUTRAL = """**340 remote branches -> 23**, and `delete_branch_on_merge` is on.

Deleted 317: 315 were the head of a merged PR, 2 had a tip already in `main`.
Kept the 3 open-PR heads and 15 closed-unmerged heads."""

# Same claim as BAD_VELOCITY, honestly labelled.
HEDGED = """A two-minute CI job on `scripts/**` might help here, though I haven't
tested whether it would actually catch a signature change like #425's. Grafting
the function onto `main` and running the suite would settle it."""

# Recommendation with conviction, nothing run.
BAD_RECOMMEND = """Emitting the pool before assignment is the right approach.
The containment gate is the bottleneck and dropping it will fix the coverage
holes across the western states."""

# Same shape, but the work was done and cited.
GOOD_MEASURED = """Emitting the pool before assignment is the right approach.
I measured it nationally: 2,689 candidates survive the trailhead-name rule and
2,470 fall inside a boundary we hold."""

# Real false positives from the calibration run over 318 messages of the
# 2026-07-28 session. Both are noun phrases, not causal claims, and both are why
# bare "fix" and "reason" came out of the trigger alternation. Locked in here so
# a future tweak to the patterns cannot quietly reintroduce them.
CAPTION = """Rendered it from the real geometry.

Left is the old rendering, right is the fix, both on South Mountain's trails."""

PROVEN = """Why this is the right line, proven on the data: every sampled case
was a real trailhead filed under the overlapping unit."""

CASES = [
    ("real 2026-07-28 assertion (must BLOCK)",        BAD_VELOCITY,   True),
    ("real 2026-07-28 correction (must PASS)",        GOOD_CORRECTION, False),
    ("ordinary report, no causal claim (must PASS)",  NEUTRAL,        False),
    ("same claim, honestly hedged (must PASS)",       HEDGED,         False),
    ("recommendation, nothing run (must BLOCK)",      BAD_RECOMMEND,  True),
    ("recommendation, measured + cited (must PASS)",  GOOD_MEASURED,  False),
    ("screenshot caption, not a claim (must PASS)",   CAPTION,        False),
    ("judgment backed by 'proven' (must PASS)",       PROVEN,         False),
]


def main() -> int:
    fails = 0
    for label, text, want_block in CASES:
        got = guard.offending(text)
        blocked = got is not None
        ok = blocked == want_block
        fails += 0 if ok else 1
        print(f"  {'ok  ' if ok else 'FAIL'} {label}")
        if blocked:
            print(f"         caught: {got[:88]}")
        if not ok:
            print(f"         expected {'BLOCK' if want_block else 'PASS'}, "
                  f"got {'BLOCK' if blocked else 'PASS'}")
    print(f"\n{len(CASES) - fails}/{len(CASES)} passed")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
