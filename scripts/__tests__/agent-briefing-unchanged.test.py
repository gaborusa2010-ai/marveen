#!/usr/bin/env python3
"""agent-briefing-build.py: an unchanged card/message set says so instead of asking
the agent to re-verify everything (cody, weekly self-check 2026-09-27)."""
import importlib.util, json, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("abb", os.path.join(HERE, "..", "agent-briefing-build.py"))
abb = importlib.util.module_from_spec(spec); spec.loader.exec_module(abb)

fails = 0
def check(name, cond):
    global fails
    print(("  ok   " if cond else "  FAIL ") + name)
    fails += 0 if cond else 1

cards = [{"id": "a1", "seq": 1, "status": "in_progress", "priority": "high", "title": "T", "updated_at": 100, "assignee": "x"}]
msgs = [{"id": 5, "from_agent": "marveen", "content": "hello"}]

# digest ignores nothing that matters and everything that does not
d0 = abb.digest(cards, msgs)
check("same input -> same digest", d0 == abb.digest(json.loads(json.dumps(cards)), msgs))
c2 = [dict(cards[0], updated_at=101)]
check("updated_at change -> new digest", d0 != abb.digest(c2, msgs))
check("new message -> new digest", d0 != abb.digest(cards, msgs + [{"id": 6}]))

# render branches
t_new = abb.render("x", cards, msgs)
t_same = abb.render("x", cards, msgs, unchanged_since="2026-09-26")
check("fresh briefing asks for staleness check", "Ha barmelyik tetel mar elavult" in t_new)
check("unchanged briefing says VALTOZATLAN", "VALTOZATLAN a 2026-09-26-i" in t_same)
check("unchanged briefing drops the re-check ask", "Ha barmelyik tetel mar elavult" not in t_same)
check("unchanged briefing still lists the cards", "#1" in t_same)
t_empty = abb.render("x", [], [], unchanged_since="2026-09-26")
check("empty briefing keeps NINCS KIOSZTOTT MUNKAD even if 'unchanged'", "NINCS KIOSZTOTT MUNKAD" in t_empty and "VALTOZATLAN" not in t_empty)

# build(): two runs in a temp STORE, API stubbed
tmp = tempfile.mkdtemp()
abb.STORE = tmp
abb.open_cards = lambda tok, a: cards
abb.open_messages = lambda tok, a: msgs
abb.build("x", "tok")
first = open(os.path.join(tmp, "briefing-x.txt")).read()
os.remove(os.path.join(tmp, "briefing-x.txt"))
abb.build("x", "tok")
second = open(os.path.join(tmp, "briefing-x.txt")).read()
check("first build is a normal briefing", "VALTOZATLAN" not in first)
check("second identical build is marked VALTOZATLAN", "VALTOZATLAN" in second)
abb.open_cards = lambda tok, a: c2
os.remove(os.path.join(tmp, "briefing-x.txt"))
abb.build("x", "tok")
third = open(os.path.join(tmp, "briefing-x.txt")).read()
check("a moved card makes it a normal briefing again", "VALTOZATLAN" not in third)

print("PASS" if not fails else "FAIL %d" % fails)
sys.exit(1 if fails else 0)
