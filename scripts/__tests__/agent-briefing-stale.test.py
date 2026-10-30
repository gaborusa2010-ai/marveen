#!/usr/bin/env python3
"""agent-briefing-build.py: a stale briefing file is set aside and rebuilt; a
fresh or evening-written one is kept (#732, cody 2026-10-30 06:00)."""
import importlib.util, os, sys, tempfile, time
from datetime import datetime, timedelta

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("abb", os.path.join(HERE, "..", "agent-briefing-build.py"))
abb = importlib.util.module_from_spec(spec); spec.loader.exec_module(abb)

fails = 0
def check(name, cond):
    global fails
    print(("  ok   " if cond else "  FAIL ") + name)
    fails += 0 if cond else 1

def touch(p, text, when):
    with open(p, "w") as f:
        f.write(text)
    t = when.timestamp()
    os.utime(p, (t, t))

now = datetime(2026, 10, 30, 6, 0)
with tempfile.TemporaryDirectory() as d:
    p = os.path.join(d, "b.txt")
    touch(p, "x", datetime(2026, 10, 29, 9, 37))
    check("yesterday 09:37 at 06:00 is stale", abb.is_stale(p, now))
    touch(p, "x", datetime(2026, 10, 29, 21, 0))
    check("evening handover (9 h) is kept", not abb.is_stale(p, now))
    touch(p, "x", datetime(2026, 10, 30, 0, 30))
    check("today's file is kept", not abb.is_stale(p, now))
    touch(p, "x", datetime(2026, 10, 30, 1, 0))
    check("today's file is kept even 22 h later the same day",
          not abb.is_stale(p, datetime(2026, 10, 30, 23, 0)))

# build(): stale file is retired and rebuilt, fresh file untouched
tmp = tempfile.mkdtemp()
abb.STORE = tmp
abb.open_cards = lambda tok, a: [{"id": "a1", "seq": 1, "status": "planned", "priority": "normal",
                                  "title": "T", "updated_at": 1, "assignee": "x"}]
abb.open_messages = lambda tok, a: []
abb.agent_waits = lambda tok, a: None
bp = os.path.join(tmp, "briefing-x.txt")
touch(bp, "OLD HANDOVER", datetime.now() - timedelta(days=2))
abb.build("x", "tok")
new = open(bp).read()
stale = [f for f in os.listdir(tmp) if f.startswith("briefing-x.txt.stale-")]
check("stale file rebuilt", "ELIGAZITAS" in new and "OLD HANDOVER" not in new)
check("stale file kept aside, not deleted", len(stale) == 1 and
      open(os.path.join(tmp, stale[0])).read() == "OLD HANDOVER")
touch(bp, "FRESH HANDOVER", datetime.now())
abb.build("x", "tok")
check("fresh file not overwritten", open(bp).read() == "FRESH HANDOVER")
# legacy name, stale -> retired, then the new file is written
os.remove(bp)
lp = os.path.join(tmp, "night-briefing-x.txt")
touch(lp, "OLD LEGACY", datetime.now() - timedelta(days=2))
abb.build("x", "tok")
check("stale legacy file retired, fresh briefing written",
      not os.path.exists(lp) and os.path.exists(bp))
# empty file still counts as missing (unchanged behaviour)
touch(bp, "", datetime.now() - timedelta(days=2))
abb.build("x", "tok")
check("empty file rebuilt in place", "ELIGAZITAS" in open(bp).read())

print("FAILS:", fails)
sys.exit(1 if fails else 0)
