#!/usr/bin/env python3
"""PostToolUse(Bash) notice: the output is exactly as long as the limit the command set.

WHY (card #580, 1c40b6bc; adopted from a marveen.io member report, post 21be0c43,
owner tg 8130): a list fetched with `head -N`, `limit=N` or `LIMIT N` is cut at N,
and a count is then stated from the cut part as if it were the whole. Our rule
("A meret is allitas", CLAUDE.md) names exactly this trap -- "egy lista hossza, amit
`limit` szabalyoz" -- but nothing in the tool path says when it happens. An output
of exactly N lines under a limit of N is the one signal visible from outside: the
list may be complete, or it may have been cut, and the command alone cannot tell.

WHAT IT DOES: reads the explicit limits from the command text, counts the stdout
lines, and when the two are equal it adds one line of additionalContext saying so.
It never blocks and never changes the tool output: the call has already run, and a
list of exactly N items is often legitimate, so the notice is a question, not a
verdict.

WHAT IT DOES NOT SEE (said here so nobody reads it as coverage):
  - a limit hidden in a variable, a script file or an interpreter body
    (`python3 -c "...[:20]"`) -- the command text is all it reads;
  - a cut output that also carries other lines (a header, a second command):
    the count no longer equals the limit, so no notice;
  - JSON on one line (`curl ...?limit=50` without formatting): one line is not
    fifty items.
Those are false negatives, which a warn-only hook can afford. A notice on a list
that merely happens to be N long is the false positive it accepts.

Limits below MIN_LIMIT are ignored: `head -1` and `limit=1` ask for one item by
design, and a notice on every one of them would teach the reader to skip it.

The Bash tool_response shape {stdout, stderr, interrupted, isImage,
noOutputExpected} and the harness's "Shell cwd was reset" note landing in stderr,
not stdout, were measured in a session transcript (toolUseResult, 2026-10-24);
that the hook payload carries the same object is assumed, not measured.

FAIL-OPEN: any error of the hook itself exits 0 with one line to
store/hook-errors.log. This hook never exits non-zero.
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
try:
    from hook_errlog import report  # noqa: E402
except Exception:  # a broken helper must not turn a notice into a failure
    def report(hook, message, exc=None):
        pass

HOOK = "limit-cut-notice"
MIN_LIMIT = 3

# `tail -n +5` starts at line 5 and is not a limit, hence no `+` in the number.
# `head -c 100` counts bytes and is left out on purpose.
LIMIT_PATTERNS = [
    ("head/tail", re.compile(r"\b(?:head|tail)\s+(?:-n\s*(\d+)|-(\d+)\b|--lines=(\d+))")),
    ("limit=", re.compile(r"\blimit=(\d+)")),
    ("--limit", re.compile(r"--limit[= ](\d+)")),
    ("LIMIT", re.compile(r"\bLIMIT\s+(\d+)", re.I)),
]


def limits_in(command):
    """Every explicit (label, N) limit the command text sets, N >= MIN_LIMIT."""
    found = []
    for label, rx in LIMIT_PATTERNS:
        for m in rx.finditer(command):
            n = int(next(g for g in m.groups() if g is not None))
            if n >= MIN_LIMIT and (label, n) not in found:
                found.append((label, n))
    return found


def line_count(stdout):
    text = stdout.rstrip("\n")
    return len(text.split("\n")) if text else 0


def notice(command, stdout):
    """The notice text, or None when the output is not exactly limit-long."""
    count = line_count(stdout)
    hits = [(label, n) for label, n in limits_in(command) if n == count]
    if not hits:
        return None
    label, n = hits[0]
    return (
        "[limit-cut-notice] The output has exactly %d lines, the same as the limit in the command (%s %d). "
        "The list may be cut there: a count taken from it is a floor, not the total, until a run "
        "without the limit (or a separate count) shows otherwise." % (count, label, n)
    )


def main():
    try:
        payload = json.load(sys.stdin)
        if payload.get("tool_name") != "Bash":
            return 0
        command = (payload.get("tool_input") or {}).get("command") or ""
        response = payload.get("tool_response") or {}
        stdout = response.get("stdout") if isinstance(response, dict) else None
        if not command or not isinstance(stdout, str):
            return 0
        text = notice(command, stdout)
        if text:
            json.dump({"hookSpecificOutput": {"hookEventName": "PostToolUse", "additionalContext": text}}, sys.stdout)
    except Exception as exc:
        report(HOOK, "notice skipped", exc)
    return 0


if __name__ == "__main__":
    sys.exit(main())
