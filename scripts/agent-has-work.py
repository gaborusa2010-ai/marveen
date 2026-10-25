#!/usr/bin/env python3
"""Does this agent have work today? The gate in front of every morning start.

Owner tg 9180 (2026-10-25, card #663): "az inditas legyen automatikus, de csak
azoknak az agenteknek akiknek van munkajuk". Before this, cody/forgacs/margo/roxy
were started at 06:00 unconditionally and five more ran all day; on 10-25 Roxy
and Forgacs answered 9 and 6 rounds between 06:00 and 07:35 -- they read an
empty-ish list and sat.

WORK means one of:
  1. an open card assigned to the agent: planned, in_progress or testing
  2. a waiting card whose date-wait (wait_kind=date) runs out before tonight's
     midnight -- that card comes back to the agent today
  3. an open inbound message that is not a receipt (same filter the briefing uses)

A waiting card on owner/card/external is NOT work: nothing the agent can do
moves it, and the owner's message to the bot wakes it anyway (#663 part 2).

Exit codes, chosen for systemd ExecCondition=:
  0    work -> the unit runs, the agent starts
  1    no work -> the unit is skipped (not a failure, nothing in "failed" state)
  0    ALSO on any read error (API down, bad JSON): an agent that idles for 30
       minutes and is stopped by the idle stopper costs little; an agent that
       had work and never started costs a day. The line says which case it was.

Usage:
  agent-has-work.py NAME            one line on stdout, exit 0/1
  agent-has-work.py --all           every name from /api/agents, a table, exit 0
"""
import importlib.util
import os
import sys
import time
from datetime import datetime, timedelta

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_spec = importlib.util.spec_from_file_location(
    "agent_briefing_build", os.path.join(REPO, "scripts", "agent-briefing-build.py"))
bb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bb)

WORK_STATUSES = ("planned", "in_progress", "testing")


def end_of_today(now=None):
    now = now or datetime.now()
    return int((datetime(now.year, now.month, now.day) + timedelta(days=1)).timestamp())


def reasons(agent, cards, msgs, now=None):
    """Pure: the list of reasons this agent has work (empty = none)."""
    eod = end_of_today(now)
    out = []
    for c in cards:
        if (c.get("assignee") or "").lower() != agent.lower() or c.get("archived_at"):
            continue
        st = c.get("status")
        if st in WORK_STATUSES:
            out.append("#%s %s" % (c.get("seq"), st))
        elif (st == "waiting" and c.get("wait_kind") == "date"
              and isinstance(c.get("wait_until"), (int, float)) and c["wait_until"] < eod):
            out.append("#%s datum ma lejar" % c.get("seq"))
    for m in msgs:
        out.append("msg %s" % m.get("id"))
    return out


def check(agent, tok):
    cards = bb._rows(bb._get("/api/kanban", tok), "cards", "items")
    msgs = bb.open_messages(tok, agent)
    return reasons(agent, cards, msgs)


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    try:
        tok = bb._token()
        if argv[0] == "--all":
            names = sorted({(a.get("name") or "") for a in bb._rows(bb._get("/api/agents", tok), "agents")} - {""})
            for n in names:
                r = check(n, tok)
                print("%-9s %s" % (n, ("VAN: " + ", ".join(r[:6]) + (" ..." if len(r) > 6 else "")) if r else "nincs"))
            return 0
        agent = argv[0]
        r = check(agent, tok)
    except Exception as e:
        print("%s %s: a munka NEM olvashato (%s), inditom" % (time.strftime("%F %T"), argv[0], e))
        return 0
    if r:
        print("%s %s: VAN munka (%d): %s" % (time.strftime("%F %T"), agent, len(r), ", ".join(r[:10])))
        return 0
    print("%s %s: NINCS munka (0 nyitott kartya, 0 ma lejaro datum, 0 lezaratlan uzenet), nem inditom"
          % (time.strftime("%F %T"), agent))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
