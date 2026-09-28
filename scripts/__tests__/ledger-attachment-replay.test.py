#!/usr/bin/env python3
"""PHOTOLEDGER901, second half: the picture comes BACK, not just in.

Capture has stored `attachment_path` for photos since 2026-09-03. What was
missing is the return trip: a restarted session was told to DOWNLOAD the
attachment through the Bot API, even when the file was already sitting on disk.

The obvious fix -- widen the tuples recent()/open_question() return -- is the
one ledger_lib's own HOOKARITAS821 note forbids doing casually: five hooks
unpack those tuples, and a caller that wraps only the call in try/except reads
the resulting ValueError as "ledger unavailable" and carries on fail-open, in
silence. So the lookup is ADDITIVE, keyed by message_id, and these tests pin
both halves of that decision:

  - the new lookup behaves (found / missing / deleted-from-disk / no id)
  - the existing tuple shapes are UNCHANGED, so no caller's arity moved

Run: python3 scripts/__tests__/ledger-attachment-replay.test.py
"""
import importlib.util
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOOKS = os.path.join(ROOT, "scripts", "hooks")
sys.path.insert(0, HOOKS)

fails = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}: got {got!r}, want {want!r}")
        fails.append(name)


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


import ledger_lib  # noqa: E402

replay = load(os.path.join(HOOKS, "ledger-replay.py"), "replay_under_test")

print("attachment_path_for: the lookup itself")
# A temp DB keeps this off the live ledger entirely -- the test must not depend
# on, or disturb, whatever the real conversation_log happens to hold today.
tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
tmpdb.close()
tmpimg = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
tmpimg.write(b"\xff\xd8\xff")  # enough to be a real file on disk
tmpimg.close()
os.environ["LEDGER_DB_PATH"] = tmpdb.name

con = ledger_lib.connect()
con.execute(
    "CREATE TABLE IF NOT EXISTS conversation_log ("
    " id INTEGER PRIMARY KEY AUTOINCREMENT, agent_id TEXT, chat_id TEXT,"
    " direction TEXT, message_id TEXT, text TEXT, ts TEXT, created_at INTEGER,"
    " attachment_kind TEXT, attachment_file_id TEXT, attachment_path TEXT)"
)
rows = [
    ("a", "c", "in", "100", "kep", "t", 1, "photo", "FID1", tmpimg.name),
    ("a", "c", "in", "101", "torolt", "t", 2, "photo", "FID2", "/nonexistent/gone.jpg"),
    ("a", "c", "in", "102", "sima", "t", 3, None, None, None),
    ("a", "c", "in", "103", "ures ut", "t", 4, "photo", "FID3", ""),
]
con.executemany(
    "INSERT INTO conversation_log (agent_id, chat_id, direction, message_id, text,"
    " ts, created_at, attachment_kind, attachment_file_id, attachment_path)"
    " VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
con.commit()
con.close()

check("a saved photo returns its path", ledger_lib.attachment_path_for("a", "100"), tmpimg.name)
# A path that no longer exists is worse than no path: the reader would go
# looking for it. None is the honest answer.
check("a path whose file is gone returns None", ledger_lib.attachment_path_for("a", "101"), None)
check("a row with no attachment returns None", ledger_lib.attachment_path_for("a", "102"), None)
check("an empty path returns None", ledger_lib.attachment_path_for("a", "103"), None)
check("an unknown message_id returns None", ledger_lib.attachment_path_for("a", "999"), None)
check("a None message_id returns None", ledger_lib.attachment_path_for("a", None), None)
check("another agent's message is not visible", ledger_lib.attachment_path_for("b", "100"), None)

print("the tuple contracts five hooks depend on are UNCHANGED")
# The whole point of the additive lookup. If either of these arities moves, the
# callers that prefix-slice would start silently reading the wrong fields.
oq = ledger_lib.open_question("a")
check("open_question still returns 6 fields", len(oq) if oq else None, 6)
oqa = ledger_lib.open_question_with_age("a")
check("open_question_with_age still returns 7", len(oqa) if oqa else None, 7)
rec = ledger_lib.recent("a", 1)
check("recent rows still have 6 fields", len(rec[0]) if rec else None, 6)

print("the replay text: local file beats download instruction")
OQ = ("8547011582", 100, "Ezt nezd meg", "2026-09-04T09:00:00Z", "photo", "FID1")
line = ['  [x] gazda: "proba"']

import json  # noqa: E402
with_path = json.dumps(replay._build_output(line, OQ, "Gazda", tmpimg.name), ensure_ascii=False)
check("names the local file", tmpimg.name in with_path, True)
check("tells the session to Read it", "Read tool" in with_path, True)
check("does NOT also tell it to download", "töltsd le és írasd át" in with_path, False)

without = json.dumps(replay._build_output(line, OQ, "Gazda", None), ensure_ascii=False)
check("falls back to download when no local file", "töltsd le és írasd át" in without, True)
check("and passes the file_id along", "FID1" in without, True)

plain = ("8547011582", 200, "sima szoveg", "2026-09-04T09:00:00Z", None, None)
p = json.dumps(replay._build_output(line, plain, "Gazda", None), ensure_ascii=False)
check("a text-only question gets neither branch",
      ("MEGVAN a gépen" in p) or ("töltsd le" in p), False)
check("but still gets the open-question block", "NYITOTT KÉRDÉS" in p, True)

os.unlink(tmpdb.name)
os.unlink(tmpimg.name)

print()
if fails:
    print(f"BUKOTT: {len(fails)} -- {', '.join(fails)}")
    sys.exit(1)
print("mind zold")
