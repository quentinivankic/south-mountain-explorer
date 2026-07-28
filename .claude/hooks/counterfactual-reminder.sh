#!/usr/bin/env bash
# UserPromptSubmit hook: re-arm CLAUDE.md gotcha #14 on EVERY turn.
#
# The Stop hook catches the violation after the fact; this is the reminder that
# prevents it. It exists because prose in CLAUDE.md is read once at session start
# and dilutes as context grows — the user's objection was precisely "I'm not
# convinced you'll still be following rules and lessons when context balloons to
# 400k plus." A UserPromptSubmit hook fires on every prompt at constant cost,
# regardless of how long the session has run, so it cannot be crowded out.
#
# Kept to a few lines on purpose: it is paid for once per turn, forever.
cat <<'EOF'
[assertion guard — CLAUDE.md #14, enforced by a Stop hook, not advisory]
Any claim that X caused Y, that X would have caught Y, or that X is the right
approach must either cite a command you actually ran in this session, or be
labelled a guess ("I think", "untested"). Naming a file, a PR number or an
identifier is not evidence. Turns that do neither are blocked before they end.
EOF
exit 0
