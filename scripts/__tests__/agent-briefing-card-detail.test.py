#!/usr/bin/env python3
"""agent-briefing-build.py (#663, owner tg 9204/9211): one line per card and no
description excerpt; the sheet names the first card and hands over the two calls
that read it fully, then skill -> work -> testing (not done) -> wait for the
review. Waiting cards stay one line with the reason; system notices are not
listed as open messages."""
import importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("abb", os.path.join(HERE, "..", "agent-briefing-build.py"))
abb = importlib.util.module_from_spec(spec); spec.loader.exec_module(abb)

fails = 0
def check(name, cond):
    global fails
    print(("  ok   " if cond else "  FAIL ") + name)
    fails += 0 if cond else 1

long_desc = "LEIRASJEL a feladatrol.\n\n" + "x" * 1000
# open_cards() order: in_progress, testing, waiting, planned; then priority.
cards = [
    {"id": "a1", "seq": 1, "status": "in_progress", "priority": "high", "title": "Dolgozz", "description": long_desc},
    {"id": "a6", "seq": 6, "status": "testing", "priority": "normal", "title": "Atnezesre"},
    {"id": "a3", "seq": 3, "status": "waiting", "priority": "normal", "title": "Var", "wait_kind": "owner",
     "wait_note": "jovahagyasra var"},
    {"id": "a4", "seq": 4, "status": "waiting", "priority": "normal", "title": "Ok nelkul"},
    {"id": "a5", "seq": 5, "status": "waiting", "priority": "normal", "title": "Datum", "wait_kind": "date",
     "wait_until": 1793008800, "wait_note": ""},
    {"id": "a2", "seq": 2, "status": "planned", "priority": "low", "title": "Ures", "description": None},
]
t = abb.render("x", cards, [])
lines = t.splitlines()

# One line per card, nothing from the description.
check("no description excerpt on the sheet", "LEIRASJEL" not in t and "Leiras:" not in t)
check("no last-comment line", "Utolso megjegyzes" not in t)
card_line = [l for l in lines if l.startswith("  #1 ")]
check("workable card is exactly one line with seq, status, priority, title, id",
      len(card_line) == 1 and "in_progress" in card_line[0] and "high" in card_line[0]
      and "Dolgozz" in card_line[0] and "(id a1)" in card_line[0])

# The order of the steps and the full-read calls for the FIRST card.
check("first card named: in_progress before planned", "Az elso dolgozhato kartyad: #1 (id a1)" in t)
tok = os.path.join(abb.STORE, ".dashboard-token")
get = 'curl -s -H "Authorization: Bearer $(cat %s)" -H "X-Agent-Id: x" %s/api/kanban/a1' % (tok, abb.DASH)
check("exact GET of the card with the agent claim", (get + "\n") in t)
check("exact GET of its comments", (get + "/comments\n") in t)
i_read, i_skill, i_work, i_done = (t.index("Olvasd el TELJESEN"), t.index("Valassz skillt EHHEZ"),
                                   t.index("3. Dolgozz"), t.index("testing-re (NEM done-ra)"))
check("steps in order: read -> skill -> work -> testing", i_read < i_skill < i_work < i_done)
check("no new card while one is in testing", "Amig egy kartyad testing-en all, uj kartyat ne kezdj" in t)
check("the sheet never tells the agent to set done", '"done"' not in t and "done-ra:" not in t)

# planned only -> the highest-priority planned card is first
t2 = abb.render("x", [{"id": "p1", "seq": 11, "status": "planned", "priority": "high", "title": "A"},
                      {"id": "p2", "seq": 12, "status": "planned", "priority": "low", "title": "B"}], [])
check("planned only: the first listed planned card is named", "Az elso dolgozhato kartyad: #11 (id p1)" in t2)

# testing is the reviewer's turn: its own block, not workable
check("testing card in its own review block", "ELLENORZESRE VAR (1)" in t and "  #6  Atnezesre  (id a6)" in t)
check("workable count excludes testing and waiting", "DOLGOZHATO KARTYAID (2)" in t)

# waiting block kept from b40a02ed
check("waiting card in its own section, with kind and note", "#3  Var: var a gazdara -- jovahagyasra var" in t)
check("waiting card without a reason says so", "#4  Ok nelkul: NINCS OK a kartyan" in t)
check("date wait shows the date", "#5  Datum: var datumra -- 10-26-ig" in t)

# Negative control: no workable card -> no steps block, no invented card id.
t3 = abb.render("x", cards[1:5], [])
check("no workable card -> no SORREND and no DOLGOZHATO", "SORREND" not in t3 and "DOLGOZHATO" not in t3)
check("no workable card -> no read call with an empty id", "/api/kanban/\n" not in t3 and "X-Agent-Id" not in t3)

# open_messages drops system senders and receipts, keeps the rest
abb._get = lambda path, tok: [
    {"id": 1, "to_agent": "x", "from_agent": "system", "status": "delivered", "content": "[CONTEXT-GUARD] ..."},
    {"id": 2, "to_agent": "x", "from_agent": "marveen", "status": "delivered", "content": "[Eredmény] msg_id:1"},
    {"id": 3, "to_agent": "x", "from_agent": "marveen", "status": "delivered", "content": "Uj feladat"},
    {"id": 4, "to_agent": "x", "from_agent": "cody", "status": "done", "content": "kesz"}]
check("only the real open message survives", [m["id"] for m in abb.open_messages("tok", "x")] == [3])

print("PASS" if not fails else "FAIL (%d)" % fails)
raise SystemExit(1 if fails else 0)
