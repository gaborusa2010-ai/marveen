#!/usr/bin/env python3
"""preCheck for the sorhatar-reggeli-javitas heartbeat (#648).

The morning round wakes the model to check three things (its SKILL.md, owner tg 7447 / 7450):
  a) the runner fired Anorak's 23:30 sorhatar-esti-ellenorzes round,
  b) a [SORHATAR] message anorak -> marveen came after it,
  c) our own line-cap-gate.py --report is empty.
When all three are clean there is nothing to fix and nothing to say, so this prints SKIP and
closes the [SORHATAR] message (notify:false), which the model would have done.
Anything else -- a missed run, no message, a hit, a different message, an API or gate error --
prints one line naming what failed, and the model is woken with it as the prompt prefix.
Always exit 0 (fail-open): a broken preCheck must wake the model, never silence it.
"""
import datetime as dt
import json
import os
import subprocess
import sys
import urllib.request

ROOT = "/home/gabor/marveen"
BASE = os.environ.get("SORHATAR_API", "http://localhost:3420")
GATE = os.environ.get("SORHATAR_GATE", os.path.join(ROOT, "scripts/hooks/line-cap-gate.py"))
ZERO = "[SORHATAR] lefutott, exit=0, 0 fajl 500 felett."


def token():
    with open(os.path.join(ROOT, "store/.dashboard-token")) as f:
        return f.read().strip()


def api(path, method="GET", body=None):
    req = urllib.request.Request(BASE + path, method=method, data=body)
    req.add_header("Authorization", "Bearer " + token())
    req.add_header("X-Agent-Id", "marveen")
    if body is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=3) as r:
        return json.loads(r.read(2_000_000) or b"null")


def window_start(now):
    """One hour before the latest 23:00 local that is not after now (the run is at 23:30)."""
    last = now.replace(hour=23, minute=0, second=0, microsecond=0)
    if last > now:
        last -= dt.timedelta(days=1)
    return (last - dt.timedelta(hours=1)).timestamp()


def check(now):
    start, end = window_start(now), now.timestamp()
    runs = api("/api/schedules/sorhatar-esti-ellenorzes/runs")
    if not isinstance(runs, list):
        return "a runs API nem listat adott: %r" % (str(runs)[:200],)
    if not any(isinstance(r, dict) and start <= r.get("ts", 0) / 1000 <= end and r.get("status") == "fired"
               for r in runs):
        return "a runner nem kuldte ki Anorak esti sorhatar-koret (nincs fired sor 22:00 ota)"

    msgs = api("/api/messages?agent=marveen&limit=200")
    if not isinstance(msgs, list):
        return "a messages API nem listat adott: %r" % (str(msgs)[:200],)
    ours = [m for m in msgs if isinstance(m, dict) and m.get("from_agent") == "anorak"
            and m.get("to_agent") == "marveen" and start <= (m.get("created_at") or 0) <= end
            and str(m.get("content", "")).startswith("[SORHATAR]")]
    if not ours:
        return "nem jott [SORHATAR] uzenet Anoraktol 22:00 ota"
    odd = [m for m in ours if m.get("content") != ZERO]
    if odd:
        return "Anorak [SORHATAR] uzenete nem a 0-fajlos: msg %s: %s" % (odd[0].get("id"), str(odd[0].get("content"))[:500])

    p = subprocess.run([sys.executable, GATE, "--report"], capture_output=True, text=True, timeout=5)
    if p.returncode != 0 or p.stdout.strip():
        return "a sajat line-cap-gate --report nem ures: exit=%d, %s" % (p.returncode, (p.stdout + p.stderr).strip()[:800])

    for m in ours:
        if m.get("status") != "done":
            api("/api/messages/%d" % int(m["id"]), "PUT", json.dumps({"status": "done", "notify": False}).encode())
    return None


def main():
    now = dt.datetime.fromtimestamp(float(os.environ["SORHATAR_NOW"])) if os.environ.get("SORHATAR_NOW") else dt.datetime.now()
    try:
        problem = check(now)
    except Exception as ex:  # fail-open: any error wakes the model
        problem = "az elozetes ellenorzes hibara futott: %s: %s" % (type(ex).__name__, str(ex)[:300])
    if problem is None:
        print("SKIP")
    else:
        print("[ELOZETES ELLENORZES] " + problem + ". A feladat tobbi lepeset vegezd el a SKILL.md szerint.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
