#!/usr/bin/env python3
"""Cards that wait on ANOTHER AGENT, from both sides, for one agent.

WHY (owner, Telegram msg 9069, 2026-10-24): #457 and #583 sat in `waiting` for
six days with "Marveen review" as a free-text reason. The dashboard pings the
waiting agent only for two kinds of wait: a blocker CARD that gets done, or a
DATE that runs out (src/web/kanban-wait-notify.ts). An `external` wait with a
note naming an agent has no trigger at all, so neither side was ever reminded.
Measured the same day: of 117 waiting cards, 42 had a blocker card, 10 a date,
65 nothing that could fire.

The fix in the rule (skill `masik-agensre-var`): ask the other agent for an ETA
and put it on the card as a `date` wait, so the existing notifier fires when the
ETA passes. This script finds the cards where that has not happened yet.

Usage
-----
  masikra-var.py cody          # what cody waits on, and who waits on cody
  masikra-var.py marveen --json
Exit 0 always when the board was read; 2 if the dashboard could not be read.
"""
import json
import os
import re
import sys
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DASH = os.environ.get("MARVEEN_DASHBOARD_URL", "http://localhost:3420")
MAIN = "marveen"
# Agents the main agent is also known as on cards (owner's assistant name).
ALIASES = {MAIN: ("marveen",)}


def _get(path):
    with open(os.path.join(REPO, "store", ".dashboard-token")) as f:
        tok = f.read().strip()
    req = urllib.request.Request(DASH + path, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _rows(payload, key):
    if isinstance(payload, dict):
        payload = payload.get(key, [])
    return payload if isinstance(payload, list) else []


def names_in(text, agents):
    """Agent names a free-text wait note mentions. Word START only: Hungarian
    suffixes the name ("Marveennel", "Codyra"), so a trailing \\b missed #406."""
    low = (text or "").lower()
    return [a for a in agents if re.search(r"\b%s" % re.escape(a), low)]


def classify(cards, agents, me, now):
    mine, on_me = [], []
    for c in cards:
        if c.get("status") != "waiting" or c.get("archived_at"):
            continue
        kind = c.get("wait_kind")
        if kind in ("owner", "card"):
            # owner: the main agent collects those; card: the notifier covers it.
            continue
        note = c.get("wait_note") or ""
        assignee = (c.get("assignee") or "").strip().lower()
        named = [a for a in names_in(note, agents) if a != assignee]
        if not named:
            continue
        until = c.get("wait_until")
        if kind == "date" and until and until > now:
            state = "ETA %s" % time.strftime("%m-%d %H:%M", time.localtime(until))
        elif kind == "date" and until:
            state = "ETA LEJART %s" % time.strftime("%m-%d %H:%M", time.localtime(until))
        else:
            state = "NINCS ETA"
        since = c.get("last_status_at") or c.get("updated_at") or now
        row = {"seq": c.get("seq"), "id": c.get("id"), "assignee": assignee, "waits_on": named,
               "state": state, "days": round((now - since) / 86400.0, 1),
               "title": (c.get("title") or "")[:70], "note": note[:120]}
        if assignee in ALIASES.get(me, (me,)):
            mine.append(row)
        if me in named:
            on_me.append(row)
    return mine, on_me


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    if not args:
        sys.stderr.write(__doc__)
        return 2
    me = args[0].strip().lower()
    try:
        cards = _rows(_get("/api/kanban"), "cards")
        agents = [a.get("name", "").lower() for a in _rows(_get("/api/agents"), "agents")]
    except Exception as e:
        sys.stderr.write("a tabla nem olvashato: %s\n" % e)
        return 2
    agents = sorted(set(agents) | {MAIN})
    mine, on_me = classify(cards, agents, me, int(time.time()))
    if "--json" in argv:
        print(json.dumps({"mine": mine, "on_me": on_me}, ensure_ascii=False, indent=1))
        return 0
    print("TE VARSZ MASIKRA (%d). NINCS ETA / LEJART -> kerdezd meg, mikor lesz kesz:" % len(mine))
    for r in mine:
        print("  #%s  %-22s %5.1f nap  vár: %s  %s" % (r["seq"], r["state"], r["days"],
                                                     ",".join(r["waits_on"]), r["title"]))
    print("RAD VARNAK (%d). Valaszolj idoponttal, vagy csinald meg:" % len(on_me))
    for r in on_me:
        print("  #%s  %-22s %5.1f nap  %s: %s" % (r["seq"], r["state"], r["days"],
                                                r["assignee"], r["title"]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
