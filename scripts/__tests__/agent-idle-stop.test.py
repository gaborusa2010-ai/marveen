#!/usr/bin/env python3
"""agent-idle-stop.py decide() (#663 part 3, owner tg 9180/9183/9185): stop only a
running sub-agent with no work, 30 quiet minutes and a pane that stood still."""
import hashlib, importlib.util, os
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ais", os.path.join(HERE, "..", "agent-idle-stop.py"))
ais = importlib.util.module_from_spec(spec); spec.loader.exec_module(ais)

fails = 0
def check(name, cond):
    global fails
    print(("  ok   " if cond else "  FAIL ") + name)
    fails += 0 if cond else 1

NOON = datetime(2026, 10, 25, 12, 0).timestamp()
PANE = "> \n"
H = hashlib.sha256(PANE.encode()).hexdigest()
base = dict(running=True, has_tmux=True, work=[], last_life=NOON - 31 * 60,
            pane_text=PANE, prev_hash=H, now=NOON)

def d(agent="brigid", **kw):
    return ais.decide(agent, **dict(base, **kw))

check("idle, no work, still pane -> stop", d()[0] is True)
check("main agent never", d("marveen")[0] is False)
check("not running -> keep", d(running=False)[0] is False)
check("no tmux -> keep", d(has_tmux=False)[0] is False)
check("work unreadable -> keep", d(work=None)[0] is False)
check("has work -> keep", d(work=["#1 planned"])[0] is False)
check("29 quiet minutes -> keep", d(last_life=NOON - 29 * 60)[0] is False)
check("exactly 30 -> stop", d(last_life=NOON - 30 * 60)[0] is True)
check("pane unreadable -> keep", d(pane_text=None)[0] is False)
busy = "working... (esc to interrupt)"
check("busy pane -> keep", d(pane_text=busy, prev_hash=hashlib.sha256(busy.encode()).hexdigest())[0] is False)
check("pane changed since last tick -> keep", d(prev_hash="other")[0] is False)
check("first tick (no previous hash) -> keep", d(prev_hash=None)[0] is False)
night = datetime(2026, 10, 25, 2, 0).timestamp()
check("anorak inside its shift -> keep", d("anorak", now=night, last_life=night - 3600)[0] is False)
check("anorak at noon behaves like the rest", d("anorak")[0] is True)
check("ariel is not exempt (tg 9185)", d("ariel")[0] is True)

print("PASS" if not fails else "FAIL (%d)" % fails)
raise SystemExit(1 if fails else 0)
