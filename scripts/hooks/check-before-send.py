#!/usr/bin/env python3
"""PreToolUse gate on the main agent's Telegram replies: the check comes BEFORE the send.

Why this exists (owner, Telegram msg 5850, 2026-10-04 15:11): "ugy latom az
ellenorzos skill meg mindig nem mukodik". Measured the same afternoon:
`require-checked.py` (Stop hook) fired 113 times that day and blocked 3 times,
so it RAN. It sat in the wrong place: a Stop hook looks at the terminal text at
the END of the turn, while the Telegram reply leaves MID-turn. On msg 5842 the
Checked block honestly said "I did not re-check the bank links and the token,
I took them from yesterday's note" -- written AFTER the list had been sent, and
only to the terminal. The owner got two already-done items as open ones.

MEASURED 2026-10-04 15:14, first live run: assistant TEXT written before a
tool call in the same message is NOT in the transcript when PreToolUse runs
(the record holds only thinking + tool_use; the text never appeared). So the
Checked block cannot travel as plain text. It travels as a Write of a file
named CHECKED_FILE (any directory, the scratchpad is fine): the Write's input
is persisted before the next tool call, and the gate reads the content from
there. A Bash command that merely contains "Checked:" does NOT count (the
false-green Cody found in require-checked, msg 4086).

What this gate enforces, for `mcp__plugin_telegram_telegram__reply` only:
  1. Since the owner's last message, the assistant has written a text block
     containing "Checked:" BEFORE this reply call.
  2. If that block has a line admitting something was NOT verified, the send is
     BLOCKED, full stop. The only way through is to check it. Leaving the claim
     out is not a fix either (owner, msg 5859, 2026-10-04 15:20: "a kihagyas nem
     old meg semmit, mert utana ugyis meg kell nezzed").
     First version (15:14) let it through if the reply SAID "I did not check";
     the owner rejected that the same afternoon, Telegram msg 5856: "ilyet ne is
     tegyel ele, hogy nem nezted meg... pont az lenne a lenyeg, hogy eloszor
     megnezed es utana valaszolsz". Telling him the gap is not the fix; closing it is.

`edit_message` is not gated: it carries in-place progress updates.
FAIL-OPEN on any internal error: a broken gate must never silence the channel.
Contract: PreToolUse, payload on stdin, exit 0 = allow, exit 2 = block.
"""
import json
import os
import re
import sys
import time

LOG = os.path.expanduser("~/.claude/hooks/check-before-send.log")
LOG_MAX = 1_000_000
CHECKED_FILE = "checked-before-send.md"

# A Checked line that admits a gap (accent-tolerant: the block may be ASCII).
UNVERIFIED_LINE = re.compile(
    r"nem\s+ellen[oő]r|nem\s+n[eé]ztem|nem\s+m[eé]rtem|nem\s+m[eé]rt\b|"
    r"\[nem ellen|unverified|not\s+verified|not\s+checked",
    re.I)

# A reply that hands the owner a list of OPEN items. Measured 2026-10-11 16:21 BST
# (owner msg 7355, "megint nem ellenorzod le magad"): the Checked block listed only
# the two claims I HAD measured; the to-do list (two items already done) was added
# from memory and simply never written into the block. A gate that only reads
# admitted gaps cannot see an omitted claim, so for this reply shape it asks for
# evidence instead: a live board/notes read in the same turn.
OPEN_LIST = re.compile(
    r"list[aá](m|r[oó]l)\b|list[aá]n\s+(([ií]gy|m[eé]g)\b|maradt)|m[eé]g\s+(ott\s+van|nyitva)|"
    r"nyitott\s+(k[aá]rty|t[eé]tel|feladat|teend)",
    re.I)
BOARD_READ = re.compile(r"/api/kanban|/api/notes|jegyzetek\.py")


def log(entry: dict) -> None:
    try:
        if os.path.isfile(LOG) and os.path.getsize(LOG) > LOG_MAX:
            os.replace(LOG, LOG + ".1")
        entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def is_owner_turn_start(rec: dict) -> bool:
    """A user record that starts a turn (prompt or channel message), not a tool result."""
    if rec.get("type") != "user":
        return False
    msg = rec.get("message") or {}
    content = msg.get("content")
    if isinstance(content, str):
        return bool(content.strip())
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return False
        return any(isinstance(b, dict) and b.get("type") == "text" for b in content)
    return False


def texts_since_turn_start(path: str) -> list:
    recs = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                recs.append(json.loads(line))
            except Exception:
                continue
    start = 0
    for i, rec in enumerate(recs):
        if is_owner_turn_start(rec):
            start = i
    out = []
    for rec in recs[start:]:
        msg = rec.get("message") or {}
        if msg.get("role") != "assistant" or not isinstance(msg.get("content"), list):
            continue
        for b in msg["content"]:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text" and b.get("text"):
                out.append(b["text"])
            # The usable carrier: a Write of CHECKED_FILE. Its content is in the
            # tool_use input, which IS persisted before the next tool runs.
            if b.get("type") == "tool_use" and b.get("name") == "Bash":
                cmd = str((b.get("input") or {}).get("command", ""))
                if BOARD_READ.search(cmd):
                    out.append("\x00BOARD_READ")
            if b.get("type") == "tool_use" and b.get("name") == "Write":
                inp = b.get("input") or {}
                if os.path.basename(str(inp.get("file_path", ""))) == CHECKED_FILE:
                    out.append(str(inp.get("content", "")))
    return out


def block(reason: str) -> int:
    sys.stderr.write(reason + "\n")
    return 2


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        if not str(payload.get("tool_name", "")).endswith("telegram__reply"):
            return 0
        reply = str((payload.get("tool_input") or {}).get("text", ""))
        path = payload.get("transcript_path")
        if not path or not os.path.isfile(path):
            log({"outcome": "allow-no-transcript"})
            return 0
        texts = texts_since_turn_start(path)
        checked = [t for t in texts if "Checked:" in t]
        if not checked:
            log({"outcome": "block-no-checked", "n_texts": len(texts), "reply_len": len(reply)})
            return block(
                "BLOKKOLVA (check-before-send): a Telegram-valasz ELOTT, ebben a korben, "
                "nincs 'Checked:' blokk. Elobb ellenorizd, amit allitani fogsz, aztan a Write "
                "eszkozzel ird a Checked: blokkot egy '" + CHECKED_FILE + "' nevu fajlba "
                "(a scratchpadba), es CSAK UTANA kuldd. A sima szoveg nem jo: a hook ezt "
                "kuldes elott nem latja. Egy sor is eleg, ha egy allitas van benne.")
        block_text = checked[-1].split("Checked:", 1)[1]
        gaps = [ln.strip() for ln in block_text.splitlines() if UNVERIFIED_LINE.search(ln)]
        if gaps:
            log({"outcome": "block-unverified", "gaps": gaps[:5], "reply_len": len(reply)})
            return block(
                "BLOKKOLVA (check-before-send): a Checked: blokkod szerint van, amit NEM "
                "ellenoriztel:\n- " + "\n- ".join(gaps[:5])
                + "\nA gazda dontese (msg 5856): eloszor megnezed, utana valaszolsz; a "
                "'nem neztem meg' az uzenetben NEM megoldas, es a kihagyas sem (msg 5859: "
                "'utana ugyis meg kell nezzed'). Ellenorizd MOST, es frissitsd a Checked: blokkot.")
        if OPEN_LIST.search(reply) and "\x00BOARD_READ" not in texts:
            log({"outcome": "block-open-list-unread", "reply_len": len(reply)})
            return block(
                "BLOKKOLVA (check-before-send): a valaszban nyitott tetelek listaja all, de "
                "ebben a korben nem futott kanban- vagy jegyzet-lekerdezes (/api/kanban, "
                "/api/notes, jegyzetek.py). A lista fejbol jott, es 10-11-en ket kesz tetelt "
                "adott at nyitottkent (gazda msg 7355). Kerdezd le a tablat MOST, vesd ossze "
                "minden tetellel, es ird be a Checked: blokkba.")
        log({"outcome": "allow", "gaps": len(gaps), "reply_len": len(reply)})
        return 0
    except Exception as exc:
        log({"outcome": "allow-error", "error": repr(exc)[:200]})
        return 0


if __name__ == "__main__":
    sys.exit(main())
