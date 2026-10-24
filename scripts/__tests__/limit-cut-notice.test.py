#!/usr/bin/env python3
"""Regression tests for scripts/hooks/limit-cut-notice.py (card #580, 1c40b6bc).

The notice must fire on the one shape it exists for -- output exactly as long as an
explicit limit in the command -- and stay quiet everywhere else, above all on the
negative control: an output SHORTER than the limit is a complete list.
The process-level cases pin the contract: exit 0 on every input, no stdout unless
there is a notice.

Run: python3 <this file>   Exit 0 = all green.
"""
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
HOOK = os.path.join(ROOT, "scripts", "hooks", "limit-cut-notice.py")

spec = importlib.util.spec_from_file_location("lcn", HOOK)
lcn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lcn)

FAILS = []


def check(name, cond):
    if not cond:
        FAILS.append(name)


def lines(n):
    return "".join("row %d\n" % i for i in range(n))


# The shape it exists for.
for cmd in ["ls | head -5", "ls | head -n 5", "ls | head -n5", "ls | head --lines=5", "git log --oneline | tail -5"]:
    check("fires: " + cmd, lcn.notice(cmd, lines(5)) is not None)
check("fires: limit=", lcn.notice('curl -s "http://x/api/memories?limit=20" | python3 fmt.py', lines(20)) is not None)
check("fires: --limit", lcn.notice("tool list --limit 10", lines(10)) is not None)
check("fires: --limit=", lcn.notice("tool list --limit=10", lines(10)) is not None)
check("fires: lowercase sql limit", lcn.notice('python3 q.py "select * from t limit 7"', lines(7)) is not None)
check("fires: no trailing newline", lcn.notice("ls | head -4", "a\nb\nc\nd") is not None)
check("names the limit", "head/tail 5" in (lcn.notice("ls | head -5", lines(5)) or ""))

# Negative control: shorter than the limit is a complete list.
check("quiet: shorter than limit", lcn.notice("ls | head -20", lines(7)) is None)
check("quiet: longer than limit (other output too)", lcn.notice("echo hdr; ls | head -5", lines(6)) is None)
check("quiet: no limit in command", lcn.notice("ls", lines(5)) is None)
check("quiet: empty output", lcn.notice("ls | head -5", "") is None)

# Not limits.
check("quiet: tail -n +5 starts at a line", lcn.notice("tail -n +5 f", lines(5)) is None)
check("quiet: head -c counts bytes", lcn.notice("head -c 5 f", lines(5)) is None)
check("quiet: below MIN_LIMIT", lcn.notice("ls | head -1", lines(1)) is None)
check("quiet: below MIN_LIMIT (2)", lcn.notice("ls | head -2", lines(2)) is None)
check("limits_in dedups", lcn.limits_in("head -5 a; head -5 b") == [("head/tail", 5)])

# Large output: a count, not a scan.
big = lines(200000)
check("fires: large output at limit", lcn.notice("seq 1 9999999 | head -200000", big) is not None)


def run(stdin_text):
    p = subprocess.run([sys.executable, HOOK], input=stdin_text, capture_output=True, text=True,
                       env=dict(os.environ, HOOK_ERRLOG_PATH=os.devnull))
    return p.returncode, p.stdout


rc, out = run(json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls | head -3"},
                          "tool_response": {"stdout": lines(3), "stderr": "", "interrupted": False}}))
check("process: exit 0 on notice", rc == 0)
try:
    ctx = json.loads(out)["hookSpecificOutput"]
    check("process: PostToolUse additionalContext", ctx["hookEventName"] == "PostToolUse" and "exactly 3 lines" in ctx["additionalContext"])
except Exception:
    FAILS.append("process: notice is valid JSON")

for name, stdin_text in [
    ("broken json", "{not json"),
    ("empty stdin", ""),
    ("not Bash", json.dumps({"tool_name": "Read", "tool_input": {"command": "head -3"}, "tool_response": {"stdout": lines(3)}})),
    ("no tool_response", json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls | head -3"}})),
    ("stdout not a string", json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls | head -3"}, "tool_response": {"stdout": None}})),
    ("tool_response is a string", json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls | head -3"}, "tool_response": lines(3)})),
    ("negative control", json.dumps({"tool_name": "Bash", "tool_input": {"command": "ls | head -9"}, "tool_response": {"stdout": lines(3)}})),
]:
    rc, out = run(stdin_text)
    check("process: exit 0, silent: " + name, rc == 0 and out == "")

if FAILS:
    print("FAIL (%d):" % len(FAILS))
    for f in FAILS:
        print("  -", f)
    sys.exit(1)
print("OK")
