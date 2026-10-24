#!/usr/bin/env python3
"""PreToolUse gate on Telegram replies: a topic the owner closed does not come back unprompted.

Why this exists (owner, Telegram msg 8813 + 8816, 2026-10-22): "ti meg mindig
hoztok vissza olyan dolgokat amit mar megbeszeltunk" / "Akkor mi a megoldas a
megbeszelt dolgokra?". Measured the same morning: docs/GAZDA-DONTESEK.md holds
228 entries in 6669 lines, and no hook read it before a send (the only hook
naming it, line-cap-gate.py, counts lines). A rule nobody can read in full
before each message is not a check.

What this gate enforces: store/lezart-ugyek.json lists the closed topics, each
with a regex and the owner's decision. If the outgoing text matches one, the
send is BLOCKED unless
  1. the owner raised it: the Telegram message that started this turn matches
     the same pattern (answering his own question is not bringing it back), or
  2. there is a new fact: the Checked file written this turn has a line
     "UJ TENY <id>: <what changed>". The line is the audit trail; an empty one
     does not count.

LIMIT, stated so nobody reads more into it: it recognises a topic by keyword.
The same topic in other words passes. The list is only as good as its upkeep.
FAIL-OPEN on any internal error or a missing/broken list: a broken gate must
never silence the channel.
Covers the Telegram reply and edit_message tools, and Bash calls of
scripts/tg-valasztas.py (the button question goes out through the Bot API).
Contract: PreToolUse, payload on stdin, exit 0 = allow, exit 2 = block.
"""
import json
import os
import re
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
TOPICS = os.environ.get("CLOSED_TOPICS_FILE") or os.path.join(ROOT, "store", "lezart-ugyek.json")
LOG = os.path.expanduser("~/.claude/hooks/closed-topic-gate.log")
LOG_MAX = 1_000_000
CHECKED_FILE = "checked-before-send.md"
NEW_FACT = re.compile(r"^\s*-?\s*UJ\s+TENY\s+([\w-]+)\s*:\s*\S", re.I | re.M)


def log(entry: dict) -> None:
    try:
        if os.path.isfile(LOG) and os.path.getsize(LOG) > LOG_MAX:
            os.replace(LOG, LOG + ".1")
        entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_topics() -> list:
    with open(TOPICS, encoding="utf-8") as fh:
        data = json.load(fh)
    out = []
    for t in data.get("ugyek", []):
        try:
            out.append((t["id"], re.compile(t["pattern"], re.I), t.get("decision", ""), t.get("what", "")))
        except Exception:
            continue
    return out


def user_text(rec: dict) -> str:
    """Text of a user record that starts a turn; '' for tool results and others."""
    if rec.get("type") != "user":
        return ""
    content = (rec.get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        if any(isinstance(b, dict) and b.get("type") == "tool_result" for b in content):
            return ""
        return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")
    return ""


def turn(path: str):
    """(owner channel text of the turn start, Checked-file contents written since)."""
    recs = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                recs.append(json.loads(line))
            except Exception:
                continue
    start, opener = 0, ""
    for i, rec in enumerate(recs):
        t = user_text(rec)
        if t.strip():
            start, opener = i, t
    owner = opener if 'source="plugin:telegram' in opener else ""
    checked = []
    for rec in recs[start:]:
        content = (rec.get("message") or {}).get("content")
        if not isinstance(content, list):
            continue
        for b in content:
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name") == "Write":
                inp = b.get("input") or {}
                if os.path.basename(str(inp.get("file_path", ""))) == CHECKED_FILE:
                    checked.append(str(inp.get("content", "")))
    return owner, "\n".join(checked)


# tg-valasztas.py sends the button question (and its --valasz reply) through the
# Bot API from Bash, so the channel matcher never sees it (Prospero, msg 11849).
# Only a call that RUNS the script: a command that merely names it (a doc edit,
# a grep) sends nothing.
TG_RUN = re.compile(r"""(?:^|[;&|(]|\s)python3?\s+(?:-\S+\s+)*["']?\S*tg-valasztas\.py\b""")
CAT_SUB = re.compile(r"""\$\(\s*cat\s+["']?([^"')\s]+)["']?\s*\)""")


def outgoing_text(payload: dict) -> str:
    """Text the call would send to the owner; '' when the call sends nothing."""
    name = str(payload.get("tool_name", ""))
    inp = payload.get("tool_input") or {}
    if name.endswith(("telegram__reply", "telegram__edit_message")):
        return str(inp.get("text", ""))
    if name == "Bash":
        cmd = str(inp.get("command", ""))
        if not TG_RUN.search(cmd):
            return ""
        parts = [cmd]
        for m in CAT_SUB.finditer(cmd):
            try:
                with open(os.path.expanduser(m.group(1)), encoding="utf-8") as fh:
                    parts.append(fh.read())
            except OSError:
                pass
        return "\n".join(parts)
    return ""


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        reply = outgoing_text(payload)
        if not reply:
            return 0
        hits = [t for t in load_topics() if t[1].search(reply)]
        if not hits:
            return 0
        path = payload.get("transcript_path")
        owner, checked = turn(path) if path and os.path.isfile(path) else ("", "")
        facts = {m.group(1).lower() for m in NEW_FACT.finditer(checked)}
        open_hits = [t for t in hits if not t[1].search(owner) and t[0].lower() not in facts]
        if not open_hits:
            log({"outcome": "allow-raised-or-new-fact", "ids": [t[0] for t in hits]})
            return 0
        log({"outcome": "block", "ids": [t[0] for t in open_hits], "reply_len": len(reply)})
        lines = "\n".join(f"- {i}: {w} (lezarta: {d})" for i, _, d, w in open_hits)
        sys.stderr.write(
            "BLOKKOLVA (closed-topic-gate): a valasz egy LEZART ugyet hoz fel, es nem a gazda "
            "kerdezett ra ebben a korben:\n" + lines + "\nA gazda, msg 8813/8816 (2026-10-22): a mar "
            "megbeszelt dolgokat ne hozzuk vissza. Hagyd ki, VAGY ha tenyleg uj teny van, ird a "
            "Checked fajlba: 'UJ TENY <id>: <mi valtozott>'. Lista: store/lezart-ugyek.json\n")
        return 2
    except Exception as exc:
        log({"outcome": "allow-error", "error": repr(exc)[:200]})
        return 0


if __name__ == "__main__":
    sys.exit(main())
