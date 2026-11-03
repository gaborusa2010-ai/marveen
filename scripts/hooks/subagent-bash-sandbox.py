#!/usr/bin/env python3
"""PreToolUse on Bash: a SUB-AGENT's shell command runs with the filesystem read-only.

Why this exists (#780, #782): on 2026-11-03 03:24 a review helper (an Agent-tool
sub-agent) ran its gate test cases in a real shell, one case was `rm -rf` of the
repo, and the repo was gone until a restore at 04:00. The owner asked (tg 10506)
why a helper has the right to delete data at all. A rule in the helper's brief
was the only guard; this hook makes it a property of the shell instead.

What it does: if the payload carries `agent_id` (measured 2026-11-03: present for
Agent-tool sub-agents, absent in the main session), the command is rewritten via
`updatedInput` to run inside scripts/probe-sandbox.sh:
  - the whole filesystem read-only, the session scratchpad writable,
  - the real HOME (read-only) and the original cwd, so reads behave as before,
  - no network (--unshare-net): a helper cannot call the dashboard's DELETE either.
The main session is untouched (no output, exit 0).

LIMIT, stated so nobody reads more into it: only the Bash tool is covered. A
sub-agent's Write/Edit tools still write wherever the permission mode allows.
FAIL-CLOSED for sub-agents: no bwrap, or a payload we cannot parse that names
an agent, blocks the call (exit 2). An unparseable payload with no agent passes.
Contract: PreToolUse, payload on stdin, exit 0 = allow (with optional JSON on
stdout), exit 2 = block.
"""
import json
import os
import shlex
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SANDBOX = os.path.join(ROOT, "scripts", "probe-sandbox.sh")


def scratch_dir(payload):
    """The session's scratch dir: /tmp/claude-<uid>/<cwd with / as ->/<session_id>."""
    sid = payload.get("session_id") or ""
    cwd = payload.get("cwd") or ""
    if not sid or not cwd or "/" in sid:
        return None
    d = os.path.join("/tmp", f"claude-{os.getuid()}", cwd.replace("/", "-"), sid)
    return d if os.path.isdir(d) else None


def main():
    raw = sys.stdin.read()
    try:
        payload = json.loads(raw)
    except ValueError:
        if '"agent_id"' in raw:
            print("subagent-bash-sandbox: unreadable sub-agent payload, blocked", file=sys.stderr)
            return 2
        return 0
    if not payload.get("agent_id") or payload.get("tool_name") != "Bash":
        return 0
    tool_input = payload.get("tool_input") or {}
    cmd = tool_input.get("command")
    if not isinstance(cmd, str) or not cmd.strip():
        return 0
    if shutil.which("bwrap") is None or not os.path.isfile(SANDBOX):
        print("subagent-bash-sandbox: no sandbox (bwrap or probe-sandbox.sh missing); "
              "a sub-agent's shell never runs unsandboxed, blocked", file=sys.stderr)
        return 2
    env = ["PROBE_REAL_HOME=1", "PROBE_QUIET=1"]
    cwd = payload.get("cwd")
    if cwd:
        env.append("PROBE_CWD=" + shlex.quote(cwd))
    scratch = scratch_dir(payload)
    if scratch:
        env.append("PROBE_RW=" + shlex.quote(scratch))
    wrapped = " ".join(env) + " bash " + shlex.quote(SANDBOX) + " bash -c " + shlex.quote(cmd)
    new_input = dict(tool_input, command=wrapped)
    print(json.dumps({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "allow",
        "permissionDecisionReason": "sub-agent shell: read-only filesystem sandbox (#782)",
        "updatedInput": new_input,
    }}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
