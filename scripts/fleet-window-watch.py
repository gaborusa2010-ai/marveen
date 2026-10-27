#!/usr/bin/env python3
"""The FELOLDAS-FIGYELo: says out loud when a stopped agent could work again.

THE GAP IT CLOSES
-----------------
The budget gates stop sub-agents, and nothing starts them back up. That half is
deliberate -- the owner's standing decision, quoted in scripts/fleet-safe-start.sh:
"Commit 3 utan se inditsd automatikusan a teljes flottat". What was missing is
the OTHER half: nobody said when the reason for the stop had gone away.

Anorak measured the cost on 2026-09-19: Cody stood 28 hours, Forgacs and Roxy
23 each, daily-log rows zero, while the stop notice had promised they would come
back at 06:00. The promise was removed the same night. This script is what
replaces it -- a NOTICE, not an automatic start.

The owner chose this shape on 2026-09-23 (Telegram msg 3774: "1. Legyen
figyelo"), against the alternative of auto-starting. Both Anorak and Marveen
recommended the notice, and the recommendation is recorded in
docs/GAZDA-DONTESEK.md.

WHAT COUNTS AS "COULD WORK AGAIN"
---------------------------------
All three at once, and the third is the one that keeps this quiet:

  1. a gate stopped the agent (fleet-daily-budget.json `stopped`, or
     agent-budget-guard.json `stopped_at`),
  2. it is NOT running now (runState from the dashboard, never `status`, which
     is the CONFIG state and reads "configured" for a running agent too --
     measured 2026-09-23, and it fooled this author first),
  3. the stop happened in an EARLIER window-day than now. The window is anchored
     to 06:00, the hour the weekly allowance resets, exactly like the gate's own
     day key -- a calendar date would fire between midnight and 06:00, while the
     ceiling that stopped the agent is still the same one.

AND IT ANSWERS "IS THERE ANYTHING FOR THEM", not just "they are down". The
owner's third instruction in the same message was "adj nekik munkat ha nincs mit
csinaljanak", so the notice carries each agent's open card and open message
count. A restart notice with no backlog behind it is a chore, not information.

2026-10-25: FROM NOTICE TO FAILURE ALARM (#663, owner tg 9191)
---------------------------------------------------------------
The owner reversed the 3774 shape: "ha a keret miatt allt le egy agent, akkor
masnap reggel 6-kor induljon el automatikusan (ha van munkaja)". The 06:00
marveen-<agent>-day-start units already do exactly that -- measured 10-25:
Cody, stopped by its own cap 10-24 13:24, started at 06:00:20 -- because the
gates only STOP and the start API is not gated by them. So at 06:10 a
gate-stopped agent that is still down is one of two things:
  - it has no work: agent-has-work.py skipped its start. Expected, silent.
  - it has work: the start failed. THAT is the only thing left worth saying,
    and it goes to the main agent, who can look at the unit log and fix it,
    not to the owner as a chore. The owner only hears it if that send fails.
Anorak is never a candidate: its start is the 23:00 night shift, not 06:00.

Usage
-----
  fleet-window-watch.py            # measure and print, send nothing
  fleet-window-watch.py --notify   # send one alarm per window-day
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(REPO, "store")
STATE = os.path.join(STORE, "fleet-window-watch.json")
FLEET_STATE = os.path.join(STORE, "fleet-daily-budget.json")
GUARD_STATE = os.path.join(STORE, "agent-budget-guard.json")
DASH = os.environ.get("MARVEEN_DASHBOARD_URL", "http://localhost:3420")
SUBS = ("anorak", "cody", "forgacs", "margo", "roxy")
NIGHT_SHIFT = ("anorak",)  # started at 23:00 by its own timer, never at 06:00
DAILY_FROM = 6  # the anchor hour, same as the gates'


def _guard():
    """Reuse the per-agent guard's notify() instead of restating it -- one owner
    for "how do we reach the owner", for the same reason fleet-daily-budget.py
    imports it rather than copying it."""
    src = os.path.join(REPO, "scripts", "agent-budget-guard.py")
    spec = importlib.util.spec_from_file_location("agent_budget_guard", src)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def window_day(now=None, daily_from=DAILY_FROM):
    now = now or datetime.now()
    if now.hour < daily_from:
        now = now - timedelta(days=1)
    return now.strftime("%Y-%m-%d")


def _read_json(path, default):
    try:
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, type(default)) else default
    except (OSError, ValueError):
        return default


def _token():
    with open(os.path.join(STORE, ".dashboard-token")) as f:
        return f.read().strip()


def _get(path, tok):
    req = urllib.request.Request(DASH + path, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _rows(payload, *keys):
    if isinstance(payload, list):
        return payload
    for k in keys:
        if isinstance(payload.get(k), list):
            return payload[k]
    return []


def not_running(tok):
    """Agents whose runState is not 'running'.

    runState, NOT status. `status` is the configuration state and says
    "configured" for a perfectly healthy running agent; reading it as the run
    state reports the whole fleet as down. Measured 2026-09-23 on all five.
    """
    out = set()
    for a in _rows(_get("/api/agents", tok), "agents"):
        name = a.get("name")
        if name in SUBS and a.get("runState") != "running":
            out.add(name)
    return out


def stopped_by_a_gate(today):
    """{agent: why} for agents a gate stopped in an EARLIER window-day."""
    why = {}
    fleet = _read_json(FLEET_STATE, {})
    if fleet.get("day") and fleet["day"] < today:
        for name in fleet.get("stopped") or []:
            why[name] = "a flotta napi kerete (%s)" % fleet["day"]
    guard = _read_json(GUARD_STATE, {})
    for name, rec in guard.items():
        if name not in SUBS or not isinstance(rec, dict):
            continue
        ts = rec.get("stopped_at")
        if not ts:
            continue
        day = window_day(datetime.fromtimestamp(ts))
        if day < today:
            why.setdefault(name, "a sajat napi plafonja (%s)" % day)
    return why


def has_work(tok, agent):
    """The reasons agent-has-work.py gives -- the SAME judgment the 06:00 start
    made, imported rather than restated, so the watcher can never call an agent
    "should have started" that the start gate rightly skipped."""
    src = os.path.join(REPO, "scripts", "agent-has-work.py")
    spec = importlib.util.spec_from_file_location("agent_has_work", src)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.check(agent, tok)


def day_start_paused(agent):
    """True when the agent's 06:00 starter is switched off on purpose (a rest
    day stops marveen-<agent>-day-start.timer, 10-26 msg 13477): the start did
    not fail, it was never meant to run, so there is nothing to report."""
    try:
        r = subprocess.run(["systemctl", "--user", "is-active",
                            "marveen-%s-day-start.timer" % agent],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return False  # unknown: report, a missed start is the costlier miss
    return (r.stdout or "").strip() == "inactive"


def build_message(rows, today):
    lines = ["[INDITAS ELMARADT] %s 06:00 utan %d agens all munkaval, pedig a keret "
             "miatti leallitasa mar lejart, es a reggeli indito (#663) elinditotta volna."
             % (today, len(rows)), ""]
    for name, why, reasons in rows:
        # NO " -- " IN OUTGOING TEXT. The house style bans the em dash and its
        # two-hyphen stand-in; this text can reach the owner by the fallback
        # path, which does not pass through outgoing-copy-gate.py.
        lines.append("  %-8s leallitotta: %s. Munkaja: %s." % (name, why, ", ".join(reasons[:6])))
    lines += ["",
              "Nezd meg: journalctl --user -u marveen-<nev>-day-start, inditsd el, "
              "es ha a hiba ismetlodhet, javitsd. Errol a naprol tobbet nem szolok.",
              "Ezt a sort a fleet-window-watch.py irja."]
    return "\n".join(lines)


def send_to_main(text):
    """To the main agent's inbox; True only on a confirmed id (house rule)."""
    try:
        r = subprocess.run(["bash", os.path.join(REPO, "scripts", "agent-msg.sh"),
                            "marveen", "marveen", "-"], input=text,
                           capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return False
    return "OK id=" in (r.stdout or "")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args(argv)

    today = window_day()
    st = _read_json(STATE, {})
    already = set(st.get("notified") or []) if st.get("day") == today else set()

    tok = _token()
    down = not_running(tok)
    why = stopped_by_a_gate(today)
    names = sorted(down & set(why))

    rows = []
    for n in names:
        if n in NIGHT_SHIFT or day_start_paused(n):
            continue
        reasons = has_work(tok, n)
        if reasons:  # no work = its start was rightly skipped, nothing to say
            rows.append((n, why[n], reasons))

    print(json.dumps({"window_day": today,
                      "not_running": sorted(down),
                      "gate_stopped": why,
                      "candidates": [r[0] for r in rows],
                      "already_notified": sorted(already)}, ensure_ascii=False))

    fresh = [r for r in rows if r[0] not in already]
    if not fresh:
        return 0
    if not a.notify:
        print(build_message(fresh, today))
        return 0
    text = build_message(fresh, today)
    if send_to_main(text) or _guard().notify(text, to_owner=True):
        _write_state({"day": today, "notified": sorted(already | {r[0] for r in fresh})})
    else:
        # NOT recorded: an unsent notice must be retried, never marked done.
        sys.stderr.write("fleet-window-watch: az ertesites NEM ment ki, az allapot "
                         "valtozatlan, a kovetkezo futas ujraprobalja\n")
        return 1
    return 0


def _write_state(d):
    os.makedirs(STORE, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False)
    os.replace(tmp, STATE)


if __name__ == "__main__":
    sys.exit(main())
