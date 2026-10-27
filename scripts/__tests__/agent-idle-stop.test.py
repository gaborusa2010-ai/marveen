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

# Daily 1-hour slots (owner tg 9343/9351/9353)
def ts(day, hh, mm):
    return datetime(2026, 10, day, hh, mm).timestamp()   # 10-26 is a Monday
DAILY = [{"days": "*", "start": "08:00", "minutes": 60}]
WEEKDAY = [{"days": [0, 1, 2, 3, 4], "start": "09:00", "minutes": 60}]
MONTHLY = [{"days": "*", "dom": [1], "start": "09:00", "minutes": 60}]
check("started a minute early -> slot end 09:00", ais.slot_end(DAILY, ts(26, 7, 59)) == ts(26, 9, 0))
check("started inside the slot -> slot end", ais.slot_end(DAILY, ts(26, 8, 40)) == ts(26, 9, 0))
check("owner woke it at 14:00 -> no slot", ais.slot_end(DAILY, ts(26, 14, 0)) is None)
check("started 6 minutes early -> no slot", ais.slot_end(DAILY, ts(26, 7, 54)) is None)
check("weekday slot on Sunday -> no slot", ais.slot_end(WEEKDAY, ts(25, 9, 0)) is None)
check("weekday slot on Monday -> slot", ais.slot_end(WEEKDAY, ts(26, 9, 0)) == ts(26, 10, 0))
check("monthly slot off its day -> no slot", ais.slot_end(MONTHLY, ts(26, 9, 0)) is None)
check("monthly slot on the 1st", ais.slot_end(MONTHLY, datetime(2026, 11, 1, 9, 0).timestamp()) is not None)
check("no slots configured -> no slot", ais.slot_end(None, ts(26, 8, 0)) is None)
check("no session time -> no slot", ais.slot_end(DAILY, None) is None)

END = ts(26, 9, 0)
def s(now, owner_last=0, pane_text=PANE, end=END):
    return ais.decide_slot(end=end, owner_last=owner_last, pane_text=pane_text, now=now)[0]
check("not a slot session -> nothing", s(END, end=None) is None)
check("30 minutes left -> nothing", s(END - 1800) is None)
check("10 minutes left -> warn", s(END - 600) == "warn")
check("slot over, idle pane -> stop", s(END) == "stop")
check("slot over, busy pane -> grace", s(END + 60, pane_text=busy) is None)
check("grace over, busy pane -> stop", s(END + 15 * 60, pane_text=busy) == "stop")
check("slot over, pane unreadable -> stop", s(END, pane_text=None) == "stop")
check("owner wrote 5 minutes ago -> keep", s(END + 600, owner_last=END + 300) is None)
check("owner wrote 40 minutes ago -> stop", s(END + 600, owner_last=END - 1800) == "stop")
check("no inbound file -> 0", ais.owner_last_message("no-such-agent") == 0)

print("PASS" if not fails else "FAIL (%d)" % fails)
raise SystemExit(1 if fails else 0)
