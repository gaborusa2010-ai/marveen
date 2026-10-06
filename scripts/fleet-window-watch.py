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

Usage
-----
  fleet-window-watch.py            # measure and print, send nothing
  fleet-window-watch.py --notify   # send one Telegram per window-day
"""
import argparse
import importlib.util
import json
import os
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


def backlog(tok, agent):
    """(open cards, open messages) -- so the notice can say whether it is worth
    starting the agent at all."""
    cards = [c for c in _rows(_get("/api/kanban", tok), "cards", "items")
             if c.get("assignee") == agent
             and c.get("status") in ("planned", "in_progress", "waiting")
             and not c.get("archived_at")]
    msgs = [m for m in _rows(_get("/api/messages?agent=%s&limit=200" % agent, tok),
                             "messages", "items")
            if m.get("to_agent") == agent and m.get("status") != "done"
            and not (m.get("content") or "").lstrip().startswith(("[Eredmeny]", "[Eredmény]"))]
    return len(cards), len(msgs)


def build_message(rows, today):
    lines = ["FELOLDAS: a napi keret-ablak ujranyilt (%s 06:00), es %d leallitott agens "
             "allhatna munkaba." % (today, len(rows)), ""]
    for name, why, cards, msgs in rows:
        munka = ("%d nyitott kartya, %d lezaratlan uzenet" % (cards, msgs)) if (cards or msgs) \
                else "NINCS kiosztott munkaja"
        # NO " -- " IN OUTGOING TEXT. The house style bans the em dash and its
        # two-hyphen stand-in in anything sent to the owner (CLAUDE.md), and this
        # message does not pass through outgoing-copy-gate.py, which would
        # otherwise refuse it: it goes straight to the Bot API from notify().
        # A gate that cannot see the text is not a reason to drop the rule.
        lines.append("  %-8s leallitotta: %s. Nala: %s." % (name, why, munka))
    lines += ["",
              "Magamtol nem inditom el oket. A teljes flottat szandekosan nem inditjuk "
              "automatikusan (a te dontesed, lasd scripts/fleet-safe-start.sh fejlece).",
              "Ha mehet: bash scripts/fleet-safe-start.sh",
              "Errol a napról tobbet nem szolok."]
    return "\n".join(lines)


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
        cards, msgs = backlog(tok, n)
        rows.append((n, why[n], cards, msgs))

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
    if _guard().notify(build_message(fresh, today), to_owner=True):
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
