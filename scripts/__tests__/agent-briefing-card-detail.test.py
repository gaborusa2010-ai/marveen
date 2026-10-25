#!/usr/bin/env python3
"""agent-briefing-build.py (#663, owner tg 9195-9200): workable cards carry the
description head and the last comment, waiting cards one line with the reason,
system notices are not listed as open messages."""
import importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("abb", os.path.join(HERE, "..", "agent-briefing-build.py"))
abb = importlib.util.module_from_spec(spec); spec.loader.exec_module(abb)

fails = 0
def check(name, cond):
    global fails
    print(("  ok   " if cond else "  FAIL ") + name)
    fails += 0 if cond else 1

long_desc = "Elso mondat a feladatrol.\n\n" + "x" * 1000
cards = [
    {"id": "a1", "seq": 1, "status": "in_progress", "priority": "high", "title": "Dolgozz", "description": long_desc},
    {"id": "a2", "seq": 2, "status": "planned", "priority": "low", "title": "Ures", "description": None},
    {"id": "a3", "seq": 3, "status": "waiting", "priority": "normal", "title": "Var", "wait_kind": "owner",
     "wait_note": "jovahagyasra var"},
    {"id": "a4", "seq": 4, "status": "waiting", "priority": "normal", "title": "Ok nelkul"},
    {"id": "a5", "seq": 5, "status": "waiting", "priority": "normal", "title": "Datum", "wait_kind": "date",
     "wait_until": 1793008800, "wait_note": ""},
]
comments = {"a1": {"author": "roxy", "created_at": 1791624619, "content": "Itt tartok:\n" + "y" * 500}}
t = abb.render("x", cards, [], comments=comments)

check("description head is on the sheet, whitespace flattened", "Leiras: Elso mondat a feladatrol. xxx" in t)
check("description is cut at DESC_HEAD", "x" * (abb.DESC_HEAD + 1) not in t and "..." in t)
check("last comment with author", "Utolso megjegyzes (roxy, " in t and "Itt tartok: yyy" in t)
check("comment is cut at COMMENT_HEAD", "y" * (abb.COMMENT_HEAD + 1) not in t)
check("card without description gets no empty Leiras line", "Leiras: \n" not in t and t.count("Leiras:") == 1)
check("waiting card in its own section, with kind and note", "#3  Var: var a gazdara -- jovahagyasra var" in t)
check("waiting card without a reason says so", "#4  Ok nelkul: NINCS OK a kartyan" in t)
check("date wait shows the date", "#5  Datum: var datumra -- 10-26-ig" in t)
check("waiting cards are not under DOLGOZHATO", t.index("VARO KARTYAID (3)") > t.index("#2 "))
check("workable count excludes waiting", "DOLGOZHATO KARTYAID (2)" in t)
check("no workable -> no DOLGOZHATO section",
      "DOLGOZHATO" not in abb.render("x", cards[2:], [], comments={}))

# last_comments: prefers a real comment over the weekly-review stamp and automated rows,
# skips waiting cards, survives a failing fetch
rows = {"a1": [{"id": 1, "created_at": 10, "author": "roxy", "content": "valodi", "automated": 0},
               {"id": 2, "created_at": 20, "author": "gazda", "content": "[Heti átnézés] marad", "automated": 0},
               {"id": 3, "created_at": 30, "author": "system", "content": "auto", "automated": 1}],
        "a2": [{"id": 4, "created_at": 5, "author": "gazda", "content": "[Heti átnézés] marad", "automated": 0}]}
asked = []
def fake_get(path, tok):
    cid = path.split("/")[3]
    asked.append(cid)
    if cid == "a2b":
        raise OSError("boom")
    return rows.get(cid, [])
abb._get = fake_get
lc = abb.last_comments("tok", cards + [{"id": "a2b", "seq": 9, "status": "planned"}])
check("real comment beats stamp and automated", lc["a1"]["content"] == "valodi")
check("stamp used when it is the only comment", lc["a2"]["content"].startswith("[Heti"))
check("waiting cards are not fetched", "a3" not in asked and "a4" not in asked)
check("failing fetch -> no entry, no exception", "a2b" not in lc)

# open_messages drops system senders and receipts, keeps the rest
abb._get = lambda path, tok: [
    {"id": 1, "to_agent": "x", "from_agent": "system", "status": "delivered", "content": "[CONTEXT-GUARD] ..."},
    {"id": 2, "to_agent": "x", "from_agent": "marveen", "status": "delivered", "content": "[Eredmény] msg_id:1"},
    {"id": 3, "to_agent": "x", "from_agent": "marveen", "status": "delivered", "content": "Uj feladat"},
    {"id": 4, "to_agent": "x", "from_agent": "cody", "status": "done", "content": "kesz"}]
check("only the real open message survives", [m["id"] for m in abb.open_messages("tok", "x")] == [3])

print("PASS" if not fails else "FAIL (%d)" % fails)
raise SystemExit(1 if fails else 0)
