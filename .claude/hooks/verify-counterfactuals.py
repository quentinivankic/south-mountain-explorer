#!/usr/bin/env python3
"""Stop hook: refuse to end a turn that asserts an untested counterfactual.

WHY THIS EXISTS. CLAUDE.md gotcha #14 records the failure this enforces, and the
user's objection to leaving it as prose: "I'm not convinced you'll still be
following rules and lessons when context balloons to 400k plus. Make it more
durable. Make it so you have no choice but to follow it." Documentation is
advisory and dilutes as context grows. A Stop hook runs on every single turn
regardless of context size, so this is the enforcement layer and CLAUDE.md #14
is the explanation.

WHAT IT CHECKS, AND WHY IT IS NOT AN EVIDENCE DETECTOR. The obvious design —
"does the claim cite a file or a number?" — was tried against the real sentence
that caused this and FAILS. That sentence was:

    "A two-minute job on `scripts/**` and `trailforge/**` would have caught
     #425's breakage the day `road_gate` changed shape."

Backticked paths, a PR number, an identifier: every evidence marker present, and
the claim was still false. Citing artefacts is not the same as having RUN
anything. So this hook tests a different property.

A counterfactual sentence must be accompanied by one of exactly two things:

  1. a VERIFICATION CITATION — "I ran", "I tested", "measured", "31 passed":
     a statement that the work was actually done, or
  2. a HEDGE — "I think", "untested", "I haven't verified":
     an honest label that it was not.

Conviction without either is the exact defect. Nothing here judges whether the
claim is TRUE; it judges whether its confidence was earned or declared.

LOOP SAFETY. Blocks at most once per user turn, keyed on the uuid of the user
message that opened it. A second stop in the same turn always passes, so this
can never wedge a session. It also fails OPEN on any internal error — a broken
guard must not be able to block work.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile

# Sentence shapes that assert a causal or counterfactual claim. Deliberately
# narrow: each one states that something WOULD have worked, IS the cause, or IS
# the correct choice. Vague enthusiasm is not the failure mode being caught.
TRIGGERS = [
    r"\bwould have (caught|prevented|failed|worked|fixed|found|stopped|avoided|"
    r"saved|solved)\b",
    r"\bwould (catch|prevent|fix|solve|eliminate|stop|avoid)\b",
    r"\bwill (catch|prevent|fix|solve|eliminate)\b",
    # NOTE: bare "fix" and "reason" were in this alternation and were removed
    # after measuring against 318 real messages — "right is the fix" (a caption
    # under a screenshot) and "the reason is …" are noun phrases, not causal
    # claims, and they were the only false positives the guard produced.
    r"\bis the (bottleneck|root cause|real problem|right way|right approach|"
    r"right move|right call|right thing)\b",
    r"\bthe (cheapest|biggest|best|highest[- ]leverage|strongest) "
    r"(fix|lever|win|option|approach|move)\b",
    r"\bthis (is|would be) the right\b",
    r"\bthat('s| is) what (caused|broke|fixed)\b",
    r"\bthe (problem|cause|reason) (is|was)\b",
]

# "I actually did the work." Present tense of having run something.
VERIFIED = [
    r"\bI (ran|tested|measured|verified|reproduced|grafted|benchmarked|"
    r"checked by|confirmed by)\b",
    r"\bverified\b", r"\bmeasured\b", r"\breproduced\b",
    r"\bproven\b", r"\bproved\b",
    r"\b\d[\d,]* (passed|failed|tests? pass)\b",
    r"\bexit (code )?\d\b",
    r"\bagainst (the )?(real|actual|live)\b",
]

# "I did not do the work, and I am saying so."
HEDGES = [
    r"\bI think\b", r"\bI believe\b", r"\bI suspect\b", r"\bmy guess\b",
    r"\bI haven'?t (tested|run|verified|measured|checked)\b",
    r"\bnot (yet )?(tested|verified|measured|confirmed)\b",
    r"\buntested\b", r"\bunverified\b", r"\bunmeasured\b",
    r"\bwould need (testing|verifying|measuring|checking)\b",
    r"\bwould have to (test|verify|measure|check)\b",
    r"\bif .{0,40}(holds|is right|is true)\b",
    r"\bworth (testing|verifying|measuring|checking)\b",
]

_T = [re.compile(p, re.I) for p in TRIGGERS]
_V = [re.compile(p, re.I) for p in VERIFIED]
_H = [re.compile(p, re.I) for p in HEDGES]
_SENT = re.compile(r"(?<=[.!?])\s+|\n")

STATE = os.path.join(tempfile.gettempdir(), "claude-counterfactual-guard")


def last_turn(path: str) -> tuple[str, str]:
    """Return (assistant text of the current turn, uuid of the user msg that
    opened it). Walks backwards to the most recent REAL user message — tool
    results also arrive as role 'user', so they are skipped by content shape."""
    rows = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:            # noqa: BLE001 — a torn last line
                    pass

    chunks, turn_id = [], ""
    for r in reversed(rows):
        msg = r.get("message") or {}
        role = msg.get("role") or r.get("type")
        content = msg.get("content")
        if role == "user":
            blocks = content if isinstance(content, list) else []
            if any(b.get("type") == "tool_result" for b in blocks):
                continue                     # tool output, not the human
            if isinstance(content, str) and content.strip():
                turn_id = r.get("uuid", "")
                break
            if any(b.get("type") == "text" for b in blocks):
                turn_id = r.get("uuid", "")
                break
        elif role == "assistant" and isinstance(content, list):
            for b in content:
                if b.get("type") == "text" and b.get("text"):
                    chunks.append(b["text"])
    return "\n".join(reversed(chunks)), turn_id


def offending(text: str) -> str | None:
    """First sentence that asserts a counterfactual with neither a verification
    citation nor a hedge. Checked per sentence, then per message: a claim in one
    paragraph is not licensed by a number three paragraphs away, but an explicit
    'I tested this' anywhere does cover the message it introduces."""
    if any(p.search(text) for p in _V):
        return None                          # the work was cited somewhere
    for s in _SENT.split(text):
        s = s.strip()
        if not s or not any(p.search(s) for p in _T):
            continue
        if any(p.search(s) for p in _H):
            continue
        return s
    return None


def main() -> int:
    try:
        raw = sys.stdin.read()
        data = json.loads(raw) if raw.strip() else {}
    except Exception:                        # noqa: BLE001
        return 0                             # fail OPEN

    try:
        if data.get("stop_hook_active"):
            return 0
        path = data.get("transcript_path") or ""
        if not path or not os.path.exists(path):
            return 0

        text, turn_id = last_turn(path)
        if not text.strip():
            return 0

        bad = offending(text)
        if not bad:
            return 0

        # At most one block per user turn — never wedge the session.
        session = data.get("session_id") or ""
        mark = hashlib.sha256(f"{session}:{turn_id}".encode()).hexdigest()[:32]
        os.makedirs(STATE, exist_ok=True)
        flag = os.path.join(STATE, mark)
        if os.path.exists(flag):
            return 0
        open(flag, "w").close()

        reason = (
            "STOP BLOCKED — counterfactual asserted without verification "
            "(CLAUDE.md gotcha #14).\n\n"
            f"This sentence claims a cause or an outcome:\n\n    {bad[:400]}\n\n"
            "It carries neither a verification citation nor a hedge. Do ONE of "
            "these, then finish:\n\n"
            "  1. RUN the check that settles it, and cite it — the command and "
            "its actual output. Note that naming a file, a PR number or an "
            "identifier is NOT evidence; only having executed something is.\n"
            "  2. If it cannot be tested cheaply, rewrite it as a hedge — "
            "\"I think\", \"untested\" — and say what would settle it.\n"
            "  3. If it is already established earlier in this session, say so "
            "explicitly (\"I tested this above: ...\").\n\n"
            "This fires once per turn. The failure being prevented: stating an "
            "untested inference in the same voice as a measured fact, which "
            "makes the user your test harness."
        )
        print(json.dumps({"decision": "block", "reason": reason}), file=sys.stderr)
        return 2
    except Exception:                        # noqa: BLE001
        return 0                             # fail OPEN, always


if __name__ == "__main__":
    raise SystemExit(main())
