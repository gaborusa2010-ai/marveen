#!/usr/bin/env python3
"""Line-cap gate: no SKILL.md and no hand-written CLAUDE.md part may grow past 500 lines.

Owner decision, Telegram msg 7434 + 7438 + 7441 (2026-10-11, docs/GAZDA-DONTESEK.md):
the files are cut under 500 once, and from then on new decisions and lessons go to a
separate topic file, with only a one-line pointer in the capped file.

Two modes, one script:

  PreToolUse  Write|Edit|MultiEdit  -- computes the file AFTER the edit and denies it
              when the counted lines would exceed the cap AND grow. A shrinking or
              same-size edit always passes, so an already-oversized file can be cut.
  PostToolUse Bash                  -- a shell write cannot be predicted, so after every
              Bash call the capped files are re-counted against a snapshot; a file that
              grew past the cap is reported back to the model (decision: block).

What is counted:
  - SKILL.md anywhere under a `skills` directory: every line.
  - CLAUDE.md: every line OUTSIDE the `<!-- BEGIN GENERATED: ... -->` blocks. Those are
    written by src/web/agent-scaffold.ts, not by the agent, so they do not count.

Fail-open on its own errors (a broken gate must not lock every write), logged to stderr.
"""
import json
import os
import re
import sys
from pathlib import Path

CAP = int(os.environ.get("LINE_CAP", "500"))
ROOT = Path(__file__).resolve().parents[2]
GEN_RE = re.compile(r"<!-- BEGIN GENERATED:.*?<!-- END GENERATED:[^>]*-->\n?", re.S)

POINTER_HELP = (
    "Put the new text in a separate topic file instead "
    "(skill: a file next to SKILL.md, e.g. references/<topic>.md; "
    "CLAUDE.md: docs/tudas/<topic>.md) and add ONE pointer line to the capped file "
    "saying when to read it. Owner decision, Telegram msg 7441, docs/GAZDA-DONTESEK.md."
)


def vendor(path: str) -> bool:
    # Skills installed by `npx skills` (HyperFrames etc.) live under ~/.agents/skills and
    # are overwritten on every update: we do not author them, so the cap is not ours to keep.
    try:
        real = Path(path).resolve()
    except OSError:
        return False
    return (Path.home() / ".agents" / "skills") in real.parents


def kind_of(path: str):
    p = Path(path)
    if vendor(path):
        return None
    if p.name == "SKILL.md" and "skills" in p.parts:
        return "skill"
    if p.name == "CLAUDE.md":
        return "claude"
    return None


def count(text: str, kind: str) -> int:
    if kind == "claude":
        text = GEN_RE.sub("", text)
    if not text:
        return 0
    return text.count("\n") + (0 if text.endswith("\n") else 1)


def read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError):
        return ""


def apply_edit(text: str, old: str, new: str, replace_all: bool) -> str:
    if not old:
        return text
    return text.replace(old, new) if replace_all else text.replace(old, new, 1)


def after_text(tool: str, inp: dict, before: str):
    if tool == "Write":
        return inp.get("content", "")
    if tool == "Edit":
        return apply_edit(before, inp.get("old_string", ""), inp.get("new_string", ""),
                          bool(inp.get("replace_all")))
    if tool == "MultiEdit":
        text = before
        for e in inp.get("edits") or []:
            text = apply_edit(text, e.get("old_string", ""), e.get("new_string", ""),
                              bool(e.get("replace_all")))
        return text
    return None


def pre(data: dict) -> int:
    tool = data.get("tool_name", "")
    inp = data.get("tool_input") or {}
    path = inp.get("file_path") or ""
    kind = kind_of(path)
    if not kind:
        return 0
    before = read(path)
    after = after_text(tool, inp, before)
    if after is None:
        return 0
    n_before, n_after = count(before, kind), count(after, kind)
    if n_after <= CAP or n_after <= n_before:
        return 0
    what = "SKILL.md" if kind == "skill" else "CLAUDE.md (hand-written part, generated blocks excluded)"
    reason = (f"LINE-CAP: {path} would grow to {n_after} lines (now {n_before}, cap {CAP}) -- {what}. "
              + POINTER_HELP)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason}}))
    return 0


def capped_files():
    home = Path.home()
    globs = [(home / ".claude" / "skills", "*/SKILL.md"),
             (ROOT / ".claude" / "skills", "*/SKILL.md"),
             (ROOT, "agents/*/.claude/skills/*/SKILL.md"),
             (ROOT, "CLAUDE.md"),
             (ROOT, "agents/*/CLAUDE.md")]
    seen = set()
    for base, pat in globs:
        for f in base.glob(pat):
            try:
                real = str(f.resolve())
            except OSError:
                continue
            if real not in seen:
                seen.add(real)
                yield real


def measure_all() -> dict:
    out = {}
    for f in capped_files():
        k = kind_of(f)
        if k:
            out[f] = count(read(f), k)
    return out


def snapshot_path(data: dict) -> Path:
    # One snapshot per working directory: agents run in parallel, and a shared
    # snapshot would let one agent's hook swallow another agent's growth.
    tag = re.sub(r"[^A-Za-z0-9]+", "-", data.get("cwd") or os.getcwd()).strip("-")[-80:]
    return ROOT / "store" / "line-cap" / f"snapshot-{tag}.json"


def post(data: dict) -> int:
    SNAPSHOT = snapshot_path(data)
    now = measure_all()
    try:
        prev = json.loads(SNAPSHOT.read_text())
    except (FileNotFoundError, ValueError):
        prev = None
    try:
        SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT.write_text(json.dumps(now, indent=0, sort_keys=True))
    except OSError as e:
        print(f"line-cap-gate: snapshot write failed: {e}", file=sys.stderr)
    if prev is None:
        return 0
    grown = [(f, prev.get(f, 0), n) for f, n in now.items() if n > CAP and n > prev.get(f, 0)]
    if not grown:
        return 0
    lines = "; ".join(f"{f}: {a} -> {b}" for f, a, b in grown)
    print(json.dumps({"decision": "block",
                      "reason": f"LINE-CAP (after Bash): over {CAP} lines and grew: {lines}. "
                                "Move the added text out and leave a pointer line (if you did not write it, another agent did: report it, do not cut it). " + POINTER_HELP}))
    return 0


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--report":
        bad = {f: n for f, n in measure_all().items() if n > CAP}
        for f, n in sorted(bad.items(), key=lambda x: -x[1]):
            print(f"{n}\t{f}")
        return 1 if bad else 0
    try:
        data = json.load(sys.stdin)
    except ValueError:
        return 0
    try:
        if data.get("hook_event_name") == "PostToolUse":
            return post(data)
        return pre(data)
    except Exception as e:  # fail-open, but say so
        print(f"line-cap-gate: internal error, allowing: {e!r}", file=sys.stderr)
        return 0


if __name__ == "__main__":
    sys.exit(main())
