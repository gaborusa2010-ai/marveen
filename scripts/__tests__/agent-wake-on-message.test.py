#!/usr/bin/env python3
"""agent-wake-on-message.py wakes() (#663 part 2, owner tg 9180): a stopped agent
starts on a private owner message, or a group message that names it."""
import importlib.util, os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("awm", os.path.join(HERE, "..", "agent-wake-on-message.py"))
awm = importlib.util.module_from_spec(spec); spec.loader.exec_module(awm)

fails = 0
def check(name, cond):
    global fails
    print(("  ok   " if cond else "  FAIL ") + name)
    fails += 0 if cond else 1

OWNER = "1000"  # fixture id, not a real user
def msg(text, chat="private", sender=OWNER, key="message"):
    return {key: {"message_id": 7, "from": {"id": int(sender)}, "chat": {"type": chat}, "text": text}}

A = {OWNER}
check("private owner message wakes", awm.wakes("kiron", [msg("szia")], A) is not None)
check("stranger in private does not", awm.wakes("kiron", [msg("szia", sender="111")], A) is None)
check("empty allowFrom wakes nobody", awm.wakes("kiron", [msg("szia")], set()) is None)
check("group message for someone else does not", awm.wakes("kiron", [msg("Cody, nezd meg", chat="supergroup")], A) is None)
check("group message naming the agent wakes", awm.wakes("kiron", [msg("Kiron, mi a helyzet?", chat="supergroup")], A) is not None)
check("group message naming the display name wakes",
      awm.wakes("kiron", [msg("Merlinke figyelj", chat="group")], A, extra_names=("Merlinke",)) is not None)
check("photo caption counts", awm.wakes("kiron", [{"message": {"message_id": 8, "from": {"id": int(OWNER)},
      "chat": {"type": "group"}, "caption": "kiron ezt nezd"}}], A) is not None)
check("no updates -> none", awm.wakes("kiron", [], A) is None)
check("update without message (callback etc.) is skipped", awm.wakes("kiron", [{"callback_query": {}}], A) is None)
check("short extra name is ignored (no 'al' matching everything)",
      awm.wakes("kiron", [msg("valami mas", chat="group")], A, extra_names=("al",)) is None)

print("PASS" if not fails else "FAIL (%d)" % fails)
raise SystemExit(1 if fails else 0)
