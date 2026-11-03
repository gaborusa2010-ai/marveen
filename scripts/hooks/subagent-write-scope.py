#!/usr/bin/env python3
"""PreToolUse on Write/Edit/NotebookEdit: a SUB-AGENT writes only to scratch or a side worktree.

Companion of subagent-bash-sandbox.py (#782). That hook makes a sub-agent's shell
read-only; this one closes the other path a sub-agent has to change files, its
file tools. Allowed targets for a payload with `agent_id`:
  - the session's scratch dir (/tmp/claude-<uid>/<cwd with / as ->/<session_id>),
  - any git worktree of this repo OTHER than the main checkout (review fixes, branches).
Everything else is blocked, the main checkout included. The main session is untouched.

FAIL-CLOSED for sub-agents: a missing or relative path, or a payload we cannot
parse that names an agent, blocks. Contract: PreToolUse, exit 0 = allow, 2 = block.
"""
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOOLS = {"Write", "Edit", "MultiEdit", "NotebookEdit"}


def scratch_dir(payload):
    sid = payload.get("session_id") or ""
    cwd = payload.get("cwd") or ""
    if not sid or not cwd or "/" in sid:
        return None
    return os.path.join("/tmp", f"claude-{os.getuid()}", cwd.replace("/", "-"), sid)


def side_worktrees():
    try:
        out = subprocess.run(["git", "-C", ROOT, "worktree", "list", "--porcelain"],
                             capture_output=True, text=True, timeout=3).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    trees = [l[len("worktree "):] for l in out.splitlines() if l.startswith("worktree ")]
    return [os.path.realpath(t) for t in trees[1:]]  # the first entry is the main checkout


def inside(path, base):
    return path == base or path.startswith(base.rstrip("/") + "/")


def block(msg):
    print("subagent-write-scope: " + msg, file=sys.stderr)
    return 2


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except ValueError:
        return block("unreadable sub-agent payload, blocked") if '"agent_id"' in raw else 0
    if not payload.get("agent_id") or payload.get("tool_name") not in TOOLS:
        return 0
    ti = payload.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path")
    if not isinstance(path, str) or not os.path.isabs(path):
        return block("no absolute target path, blocked")
    real = os.path.realpath(path)
    scratch = scratch_dir(payload)
    if scratch and inside(real, os.path.realpath(scratch)):
        return 0
    if any(inside(real, wt) for wt in side_worktrees()):
        return 0
    return block(f"a sub-agent may write only to the session scratchpad or a side worktree "
                 f"(#782); {path} is outside both. Write the result to the scratchpad and "
                 f"report it; the main agent applies it.")


if __name__ == "__main__":
    sys.exit(main())
