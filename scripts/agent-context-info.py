#!/usr/bin/env python3
"""Print one agent's context size and uptime from a /api/agents JSON on stdin.

Separate from agent-runstate.py on purpose: that one is called by the start
guard and must keep printing exactly one word. This one is for the report.

Why it exists (2026-09-22): the night-start watch could say "the agent is up"
but not "the agent starts the night carrying the whole evening". Marveen had to
measure contextTokens by hand twice before this was written -- and on 09-22 it
was 207 831 tokens after 5h48m of uptime, which is the number that decides
whether a restart is worth asking for. A field that has to be measured by hand
every night is a field the report should print.

Output is one line, empty if the agent is absent or the JSON unreadable:
  runningSince=2026-09-22 17:17:24 (5h 48m) | contextTokens=207831
"""
import json, os, sys, datetime

name = os.environ.get("AGENT", "")
try:
    rows = json.load(sys.stdin)
except Exception:
    sys.exit(0)
if not isinstance(rows, list):
    sys.exit(0)

for a in rows:
    if a.get("name") != name:
        continue
    parts = []
    since = a.get("runningSince")
    if since:
        try:
            t = datetime.datetime.fromtimestamp(float(since))
            mins = int((datetime.datetime.now() - t).total_seconds() // 60)
            parts.append("runningSince=%s (%dh %02dm)" % (
                t.strftime("%Y-%m-%d %H:%M:%S"), mins // 60, mins % 60))
        except Exception:
            pass
    ctx = a.get("contextTokens")
    if isinstance(ctx, int):
        parts.append("contextTokens=%d" % ctx)
    if parts:
        print(" | ".join(parts))
    break
