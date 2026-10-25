#!/usr/bin/env python3
"""PreToolUse gate: a question to the owner waits for a decision-log lookup (card #664).

Why this exists (owner, Telegram msg 9199, 2026-10-25): most errors come from
forgetting something already discussed and recorded. Measured the same day:
five "already discussed" cases in docs/GAZDA-DONTESEK.md, and in each one the
decision WAS on the page; nobody looked at it in the moment. "Look before you
ask" had been a CLAUDE.md rule for weeks. A rule that runs on good will is not
a check, so this gate makes the lookup a precondition of the question.

What this gate enforces: an outgoing QUESTION to the owner is BLOCKED unless
scripts/dontes-kereso.py ran for this agent AFTER the owner's last incoming
Telegram message. A question is
  - a Telegram reply / edit_message whose text has a '?' outside URLs and code,
  - a Bash call that runs scripts/tg-valasztas.py (the button question), but
    NOT its '--valasz' form, which closes a question instead of asking one.
Everything else passes: reports and answers are not gated (a false block
silences the channel, so the net is kept narrow on purpose).

"The owner's last incoming message" is read from the session transcript, the
same way closed-topic-gate.py learns the turn start: the last user record
(not a tool result) carrying a <channel source="plugin:telegram ...> tag, or a
queued_command attachment carrying one (a message that arrived mid-turn). With
no Telegram message in the transcript (a scheduled task asking proactively),
the turn start is the baseline instead.
"Ran for this agent": a run in store/dontes-kereso/<agent>.jsonl, the agent
resolved the same way as the script does (MARVEEN_AGENT, agents/<name> in the
cwd, in TELEGRAM_STATE_DIR, else marveen), or a run of ANY agent recorded with
this session's id (the Bash cwd can move; the session id does not).

LIMITS, stated so nobody reads more into them: the gate checks THAT a lookup
ran, not that it was about this question; the lookup is keyword-only; the agent
identity is self-asserted.
FAIL-OPEN on any internal error, a missing transcript or a broken lookup
module: a broken gate must never silence the channel.
Contract: PreToolUse, payload on stdin, exit 0 = allow, exit 2 = block.
"""
import importlib.util
import json
import os
import re
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
KERESO = os.path.join(ROOT, "scripts", "dontes-kereso.py")
LOG = os.path.expanduser(os.environ.get("DONTES_GATE_LOG") or "~/.claude/hooks/dontes-lookup-gate.log")
LOG_MAX = 1_000_000

# Same recogniser as closed-topic-gate.py: only a call that RUNS the script
# sends anything; a grep or a doc edit that names it does not.
TG_RUN = re.compile(r"""(?:^|[;&|(]|\s)python3?\s+(?:-\S+\s+)*["']?\S*tg-valasztas\.py\b["']?""")
TG_CLOSE = re.compile(r"""^\s*(?:--agent\s+\S+\s+)?--valasz\b""")
CHANNEL_TAG = re.compile(r'<channel\s+source="plugin:telegram')
URL = re.compile(r"https?://\S+")
CODE = re.compile(r"```.*?```|`[^`\n]*`", re.S)


def log(entry: dict) -> None:
    try:
        if os.path.isfile(LOG) and os.path.getsize(LOG) > LOG_MAX:
            os.replace(LOG, LOG + ".1")
        entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_kereso():
    spec = importlib.util.spec_from_file_location("dontes_kereso", KERESO)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def question_kind(payload: dict) -> str:
    """'' when the call asks the owner nothing, else a short label of the question shape."""
    name = str(payload.get("tool_name", ""))
    inp = payload.get("tool_input")
    if not isinstance(inp, dict):
        return ""
    if name.endswith(("telegram__reply", "telegram__edit_message")):
        text = CODE.sub(" ", URL.sub(" ", str(inp.get("text", ""))))
        return "telegram-question" if "?" in text else ""
    if name == "Bash":
        cmd = str(inp.get("command", ""))
        for m in TG_RUN.finditer(cmd):
            if not TG_CLOSE.match(cmd[m.end():]):
                return "tg-valasztas"
    return ""


def parse_ts(value) -> float:
    if not isinstance(value, str) or not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


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


def baseline(path: str):
    """(epoch of the owner's last inbound Telegram message, or of the turn start; label)."""
    owner_t, turn_t = 0.0, 0.0
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if not isinstance(rec, dict):
                continue
            t = parse_ts(rec.get("timestamp"))
            text = user_text(rec)
            if rec.get("type") == "attachment":
                att = rec.get("attachment") or {}
                if isinstance(att, dict) and att.get("type") == "queued_command":
                    text = str(att.get("prompt", ""))
            if not text.strip() or not t:
                continue
            if CHANNEL_TAG.search(text):
                owner_t = max(owner_t, t)
            if rec.get("type") == "user":
                turn_t = max(turn_t, t)
    if owner_t:
        return owner_t, "owner-message"
    return turn_t, "turn-start"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    try:
        if not isinstance(payload, dict):
            return 0
        kind = question_kind(payload)
        if not kind:
            return 0
        path = payload.get("transcript_path")
        if not path or not os.path.isfile(path):
            log({"outcome": "allow-no-transcript", "kind": kind})
            return 0
        since, basis = baseline(path)
        if not since:
            log({"outcome": "allow-no-baseline", "kind": kind})
            return 0
        k = load_kereso()
        agent = k.agent_name(str(payload.get("cwd") or ""))
        session = str(payload.get("session_id") or "")
        runs = k.last_runs(agent)
        if session:
            runs += [r for r in k.all_runs() if r.get("session") == session]
        after = [r for r in runs if r["t"] > since]
        if after:
            last = max(after, key=lambda r: r["t"])
            log({"outcome": "allow-looked-up", "kind": kind, "agent": agent, "basis": basis,
                 "terms": last.get("terms"), "hits": last.get("hits")})
            return 0
        before = max((r["t"] for r in runs), default=0.0)
        log({"outcome": "block", "kind": kind, "agent": agent, "basis": basis,
             "since": since, "last_run": before or None})
        when = ("a legutobbi keresesed a gazda utolso uzenete ELOTT futott"
                if before else "ebben a helyzetben meg nem futott dontes-kereses")
        sys.stderr.write(
            "BLOKKOLVA (dontes-lookup-gate): KERDEST kuldenel a gazdanak, de " + when + ".\n"
            "A gazda, msg 9199 (2026-10-25): a legtobb hiba abbol jon, hogy elfelejtjuk, amit mar "
            "megbeszeltunk es leirtunk. Futtasd MOST a kerdes kulcsszavaival:\n"
            f"  python3 {KERESO} \"<2-3 kulcsszo a kerdesbol>\"\n"
            "(tobb szo = MIND kell; --any = barmelyik). Ha talal dontest, ne kerdezd meg, hanem "
            "alkalmazd; ha NINCS TALALAT, kuldd ujra a kerdest.\n")
        return 2
    except Exception as exc:
        log({"outcome": "allow-error", "error": repr(exc)[:200]})
        return 0


if __name__ == "__main__":
    sys.exit(main())
