#!/usr/bin/env python3
"""agent-has-work.py: the #663 morning gate (owner tg 9180) -- start only with work."""
import importlib.util, os, subprocess, sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("ahw", os.path.join(HERE, "..", "agent-has-work.py"))
ahw = importlib.util.module_from_spec(spec); spec.loader.exec_module(ahw)

fails = 0
def check(name, cond):
    global fails
    print(("  ok   " if cond else "  FAIL ") + name)
    fails += 0 if cond else 1

now = datetime(2026, 10, 25, 5, 59)
noon = int(datetime(2026, 10, 25, 12, 0).timestamp())
tomorrow = int(datetime(2026, 10, 26, 9, 0).timestamp())
c = lambda **k: dict({"seq": 1, "assignee": "x", "status": "planned"}, **k)

check("empty board, empty inbox -> no work", ahw.reasons("x", [], [], now) == [])
check("planned card -> work", ahw.reasons("x", [c()], [], now) == ["#1 planned"])
check("testing card -> work", ahw.reasons("x", [c(status="testing")], [], now) == ["#1 testing"])
check("assignee case-insensitive", ahw.reasons("x", [c(assignee="X")], [], now) != [])
check("someone else's card -> no work", ahw.reasons("x", [c(assignee="y")], [], now) == [])
check("archived card -> no work", ahw.reasons("x", [c(archived_at=1)], [], now) == [])
check("done card -> no work", ahw.reasons("x", [c(status="done")], [], now) == [])
check("waiting on owner -> no work",
      ahw.reasons("x", [c(status="waiting", wait_kind="owner")], [], now) == [])
check("date-wait running out today -> work",
      ahw.reasons("x", [c(status="waiting", wait_kind="date", wait_until=noon)], [], now) == ["#1 datum ma lejar"])
check("date-wait tomorrow -> no work",
      ahw.reasons("x", [c(status="waiting", wait_kind="date", wait_until=tomorrow)], [], now) == [])
check("broken wait_until (string, None) -> no crash, no work",
      ahw.reasons("x", [c(status="waiting", wait_kind="date", wait_until="x"),
                        c(status="waiting", wait_kind="date", wait_until=None)], [], now) == [])
check("None assignee -> no crash", ahw.reasons("x", [c(assignee=None)], [], now) == [])
check("open message -> work", ahw.reasons("x", [], [{"id": 7}], now) == ["msg 7"])
check("scheduled task today -> work",
      ahw.reasons("x", [], [], now, [("szamla", "09:00")]) == ["utemezett szamla 09:00"])

import json, tempfile
td = tempfile.mkdtemp()
def task(name, **cfg):
    os.makedirs(os.path.join(td, name))
    with open(os.path.join(td, name, "task-config.json"), "w") as fh:
        fh.write(cfg.pop("_raw", None) or json.dumps(cfg))
sun = datetime(2026, 11, 1, 6, 0)   # a Sunday, 06:00 = the morning start
task("vasarnap", agent="x", enabled=True, schedule="0 9 * * 0")
task("hetfo", agent="x", enabled=True, schedule="0 9 * * 1")
task("kikapcsolt", agent="x", enabled=False, schedule="0 9 * * *")
task("mase", agent="y", enabled=True, schedule="0 9 * * *")
task("reggel5", agent="X", enabled=True, schedule="0 5 * * *")
task("rossz", agent="x", enabled=True, schedule="99 9 * * *")
task("torott", _raw="{nem json")
task("ures", _raw="")
check("Sunday 09:00 task of x counted, others not (Monday, disabled, other agent, already fired, broken)",
      ahw.scheduled_today("x", sun, td) == [("vasarnap", "09:00")])
check("missing tasks dir -> empty, no crash", ahw.scheduled_today("x", sun, os.path.join(td, "nincs")) == [])
check("Monday: Sunday task not counted",
      ahw.scheduled_today("x", datetime(2026, 11, 2, 6, 0), td) == [("hetfo", "09:00")])
big = [c(seq=i) for i in range(5000)]
check("5000 cards -> counted, no crash", len(ahw.reasons("x", big, [], now)) == 5000)

# network error: the dashboard is unreachable -> start anyway (exit 0), and say so
env = dict(os.environ, MARVEEN_DASHBOARD_URL="http://127.0.0.1:9")
r = subprocess.run([sys.executable, os.path.join(HERE, "..", "agent-has-work.py"), "x"],
                   env=env, capture_output=True, text=True, timeout=60)
check("unreachable API -> exit 0 and 'NEM olvashato'", r.returncode == 0 and "NEM olvashato" in r.stdout)
r = subprocess.run([sys.executable, os.path.join(HERE, "..", "agent-has-work.py")],
                   capture_output=True, text=True, timeout=60)
check("no argument -> usage, exit 2", r.returncode == 2)

print("PASS" if not fails else "FAIL %d" % fails)
sys.exit(1 if fails else 0)
