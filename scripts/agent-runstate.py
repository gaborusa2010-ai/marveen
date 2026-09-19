#!/usr/bin/env python3
"""Print one agent's runState from a /api/agents JSON on stdin.

Split out of agent-night-shift.sh on purpose: an inline `python3 -c` inside a
double-quoted command substitution needs nested quoting that the shell parses
wrong, and the failure is a syntax error at load time -- the whole guard script
would never run.
"""
import json, os, sys
name = os.environ.get("AGENT", "")
try:
    for a in json.load(sys.stdin):
        if a.get("name") == name:
            print(a.get("runState") or "unknown")
            break
    else:
        print("missing")
except Exception:
    print("unreadable")
