#!/usr/bin/env python3
"""agent-briefing-build.py: recent undelivered (failed) messages reach the agent
through the sheet with their full text (#710/#724, Anorak fleet retro 10-26/10-28)."""
import importlib.util, os, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("abb", os.path.join(HERE, "..", "agent-briefing-build.py"))
abb = importlib.util.module_from_spec(spec); spec.loader.exec_module(abb)

fails = 0
def check(name, cond):
    global fails
    print(("  ok   " if cond else "  FAIL ") + name)
    fails += 0 if cond else 1

NOW = 1_800_000_000
def msg(i, status, age_h, sender="cody", content="kerdes", to="x"):
    return {"id": i, "to_agent": to, "from_agent": sender, "status": status,
            "created_at": NOW - age_h * 3600, "content": content}

def run(rows, now=NOW):
    abb._get = lambda path, tok: rows
    return abb.open_messages("tok", "x", now=now)

# selection
got = [m["id"] for m in run([
    msg(1, "failed", 2, content="line one\nline two"),
    msg(2, "failed", 50),                       # too old
    msg(3, "failed", 1, sender="system"),       # system notice
    msg(4, "failed", 1, content="[Eredmeny] msg_id:9 status:done"),
    msg(5, "delivered", 1),
    msg(6, "done", 1),
    msg(7, "failed", 1, to="y"),                # someone else's
    {"id": 8, "to_agent": "x", "from_agent": "cody", "status": "failed", "content": "no ts"},
])]
check("fresh failed from a real sender is kept", 1 in got)
check("failed older than 48 h is dropped", 2 not in got)
check("failed system notice is dropped", 3 not in got)
check("failed receipt is dropped", 4 not in got)
check("open delivered message still kept", 5 in got)
check("done message dropped", 6 not in got)
check("message to another agent dropped", 7 not in got)
check("failed row without created_at is not guessed in", 8 not in got)

# empty inbox
check("empty inbox -> no messages", run([]) == [])
t = abb.render("x", [], run([]))
check("empty inbox -> no NEM ERT EL section", "NEM ERT EL HOZZAD" not in t and "NINCS KIOSZTOTT MUNKAD" in t)

# render: own section, full text, not mixed into the open rows
rows = run([msg(1, "failed", 2, content="line one\nline two"), msg(5, "delivered", 1, content="nyitott")])
t = abb.render("x", [], rows)
check("failed get their own section", "NEM ERT EL HOZZAD (1)" in t)
check("full multi-line text is shown", "    line one" in t and "    line two" in t)
check("open rows count only the delivered one", "LEZARATLAN UZENETEID (1)" in t)
check("failed-only inbox is not 'no work'", "NINCS KIOSZTOTT MUNKAD" not in abb.render("x", [], run([msg(1, "failed", 2)])))

# many failed: capped, the rest counted; long text cut with the fetch hint
many = run([msg(i, "failed", 1, content="x" * (abb.FAILED_TEXT_CAP + 50) if i == 30 else "m%d" % i)
            for i in range(10, 31)])
t = abb.render("x", [], many)
check("many failed -> count is the full 21", "NEM ERT EL HOZZAD (21)" in t)
check("many failed -> only MESSAGE_CAP listed", t.count("<- cody:") == abb.MESSAGE_CAP)
check("many failed -> the rest is pointed to", "a maradek %d regebbi" % (21 - abb.MESSAGE_CAP) in t)
check("over-long text is cut with the full-text call", "[...] (teljes: GET /api/messages/30)" in t)

# network error: build() must not write a sheet that silently lacks the messages
def boom(path, tok):
    raise OSError("connection refused")
abb._get = boom
abb.STORE = tempfile.mkdtemp()
abb.open_cards = lambda tok, a: []
abb.agent_waits = lambda tok, a: None
try:
    abb.build("x", "tok")
    raised = False
except OSError:
    raised = True
check("network error raises (main() reports HIBA)", raised)
check("network error writes no briefing", not os.path.exists(os.path.join(abb.STORE, "briefing-x.txt")))

print("FAIL: %d" % fails if fails else "all ok")
sys.exit(1 if fails else 0)
