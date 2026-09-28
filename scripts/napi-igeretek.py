#!/usr/bin/env python3
"""List what was promised or decided TODAY, for the evening close to check.

Why this exists: the owner asked on 2026-09-26 (Telegram msg 4142): "az esti
zarasnal ezeket hogy nem veszed eszre? Pont ez lenne a lenyege az esti
zarasnak, hogy ne maradjon el semmi amit aznap megbeszeltunk!!!". The evening
close (scheduled task esti-kanban-atnezes) only read kanban card STATUSES, so a
promise made in chat that never became a card -- "holnap megnezem", or a
decision logged in docs/GAZDA-DONTESEK.md whose second half was never built --
was invisible to it. The same morning the promise audit (card 337) found ten
such half-finished items, all with that one shape.

This script only COLLECTS, deterministically:
  1. every docs/GAZDA-DONTESEK.md heading whose text carries today's date,
  2. every Telegram message the main agent sent today (reply tool, and the
     button helper scripts/tg-valasztas.py) that contains a promise phrase,
     with its timestamp.
KNOWN GAP, measured on day one: a button QUESTION whose text came from a file
("$(cat f)") is in neither source -- tg-valasztas.py deliberately does not log
questions to the ledger (only --valasz), and the transcript holds the command,
not the file. Msg 4138 ("Ma este megmerem") was missed this way. The answer to
such a question is logged, so the promise usually resurfaces there.

Whether each item is actually done is a judgement the evening close makes
against the cards and the live system; this script does not guess at it.

Usage:  python3 scripts/napi-igeretek.py [--date YYYY-MM-DD]
Exit 0 always; "0 tetel" is a real answer, and the header line prints how many
transcript files and outgoing messages were scanned, so an empty result from a
broken scan (zero files, zero messages) is visible as such.
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECISIONS = os.path.join(REPO, "docs", "GAZDA-DONTESEK.md")
# The main agent's config root is .channels-config (CLAUDE_CONFIG_DIR), NOT
# ~/.claude: measured 2026-09-26, the ~/.claude copy of this folder holds only
# empty files from 09-21/09-22, and the first version of this script scanned it
# and found nothing.
TRANSCRIPTS = os.path.join(
    os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(REPO, ".channels-config"),
    "projects", "-home-gabor-marveen")

# Phrases that commit to a LATER action. Matched case-insensitively, accents
# optional, because outgoing texts are written both ways.
PROMISE = re.compile(
    r"holnap|h[eé]tf[oő]n|kedden|szerd[aá]n|cs[uü]t[oö]rt[oö]k[oö]n|p[eé]nteken|szombaton|vas[aá]rnap|"
    r"ma este|este\b|k[eé]s[oő]bb|megn[eé]zem|megcsin[aá]lom|megmérem|megmerem|jelentkezem|sz[oó]lok|"
    r"be[eé]p[ií]tem|[aá]t[aá]ll[ií]tom|meg[ií]rom|felveszem|k[uü]ld[oö]m|jelzem|visszaj[oö]v[oö]k",
    re.IGNORECASE,
)


def local_day(ts):
    """ISO timestamp (UTC 'Z' or offset) -> local date string."""
    try:
        dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None, None
    loc = dt.astimezone()
    return loc.strftime("%Y-%m-%d"), loc.strftime("%H:%M")


def decisions_today(day):
    out = []
    try:
        with open(DECISIONS, encoding="utf-8") as fh:
            for n, line in enumerate(fh, 1):
                if line.startswith("#") and day in line:
                    out.append((n, line.strip()))
    except OSError as e:
        print("HIBA: %s nem olvashato: %s" % (DECISIONS, e))
    return out


def outgoing_today(day, tdir=None, replies=False, ledger=True):
    """[(HH:MM, text)] of Telegram texts sent today, plus scan counters.

    The main agent's reply texts come from the ledger (with message_id); a
    sub-agent has its own bot and is NOT in that ledger (measured 2026-09-26:
    conversation_log holds only 'marveen'), so for them replies=True reads the
    reply-tool calls straight from their transcripts.
    """
    start = datetime.datetime.strptime(day, "%Y-%m-%d").timestamp() - 86400
    files = [f for f in glob.glob(os.path.join(tdir or TRANSCRIPTS, "*.jsonl"))
             if os.path.getmtime(f) >= start]
    msgs = []
    for f in files:
        with open(f, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                d, hm = local_day(rec.get("timestamp", ""))
                if d != day:
                    continue
                content = (rec.get("message") or {}).get("content") or []
                if not isinstance(content, list):
                    continue
                for c in content:
                    if not isinstance(c, dict) or c.get("type") != "tool_use":
                        continue
                    inp = c.get("input") or {}
                    # Reply-tool texts come from the ledger below, with their
                    # message_id; the transcript only adds the button questions
                    # the ledger does not log (measured 2026-09-26: msg 4138
                    # absent from conversation_log, present here).
                    if replies and c.get("name", "").endswith("telegram__reply"):
                        msgs.append((hm, inp.get("text", "")))
                    elif c.get("name") == "Bash" and "tg-valasztas.py" in inp.get("command", ""):
                        # The text usually comes from a file ("$(cat f)"), which
                        # the transcript does not hold; the question text, when
                        # given inline, does.
                        # Only the helper's own line: the rest of a compound
                        # command is code, and code is full of "este" and dates.
                        for m in re.finditer(r"tg-valasztas\.py([^\n]*)", inp.get("command", "")):
                            msgs.append((hm, m.group(1)))
    # The outbound ledger (scripts/hooks/ledger_lib.py, conversation_log): the
    # reply tool and tg-valasztas --valasz both write it, with the message_id.
    if not ledger:
        msgs.sort()
        return files, msgs
    try:
        sys.path.insert(0, os.path.join(REPO, "scripts", "hooks"))
        import ledger_lib
        con = ledger_lib.connect()
        lo = datetime.datetime.strptime(day, "%Y-%m-%d").timestamp()
        for mid, ts, text in con.execute(
                "SELECT message_id, ts, text FROM conversation_log WHERE agent_id='marveen'"
                " AND direction='out' AND created_at >= ? AND created_at < ?",
                (lo, lo + 86400)):
            _, hm = local_day(ts or "")
            msgs.append(("%s #%s" % (hm, mid), text or ""))
        con.close()
    except Exception as e:  # a missing ledger must be visible, not an empty day
        print("HIBA: a kimeno naplo (conversation_log) nem olvashato: %s" % e)
    msgs.sort()
    return files, msgs


def api(path):
    import urllib.request
    tok = open(os.path.join(REPO, "store", ".dashboard-token")).read().strip()
    req = urllib.request.Request("http://localhost:3420" + path,
                                 headers={"Authorization": "Bearer " + tok})
    return json.load(urllib.request.urlopen(req, timeout=20))


def promises(msgs):
    hits = []
    for hm, text in msgs:
        for sent in re.split(r"(?<=[.!?])\s+|\n+", text):
            if PROMISE.search(sent):
                hits.append((hm, sent.strip()[:220]))
    return hits


def team_today(day, cap=8):
    """Per sub-agent: daily-log entries, promise sentences in their own Telegram
    replies, and in the inter-agent messages they SENT today."""
    try:
        agents = api("/api/agents")
        agents = agents if isinstance(agents, list) else agents.get("agents", [])
        names = [a.get("name") for a in agents if a.get("name")]  # `name`, not `id`
    except Exception as e:
        print("HIBA: /api/agents nem olvashato: %s" % e)
        return
    lo = datetime.datetime.strptime(day, "%Y-%m-%d").timestamp()
    # Sub-agents' .claude-config/projects is a symlink to this (measured 2026-09-26).
    projects = os.path.expanduser("~/.claude/projects")
    for n in names:
        tdir = os.path.join(projects, "-home-gabor-marveen-agents-" + n)
        files, tg = outgoing_today(day, tdir=tdir, replies=True, ledger=False)
        try:
            log = api("/api/daily-log?agent=%s&date=%s" % (n, day))
        except Exception as e:
            log = []
            print("   HIBA: %s napi naplo: %s" % (n, e))
        sent = []
        try:
            rows = api("/api/messages?agent=%s&limit=200" % n)
            rows = rows if isinstance(rows, list) else rows.get("messages", [])
            if len(rows) >= 200 and min((r.get("created_at") or 0) for r in rows) >= lo:
                # The window is capped; if even its oldest row is from today,
                # earlier messages of the day fell off and the count is short.
                print("   FIGYELEM: %s -- a 200 soros ablak nem er vissza a nap elejeig" % n)
            for r in rows:
                ca = r.get("created_at") or 0
                if r.get("from_agent") == n and lo <= ca < lo + 86400:
                    hm = datetime.datetime.fromtimestamp(ca).strftime("%H:%M")
                    sent.append(("%s ->%s #%s" % (hm, r.get("to_agent"), r.get("id")),
                                 r.get("content") or ""))
        except Exception as e:
            print("   HIBA: %s uzenetei: %s" % (n, e))
        hits = promises(tg) + promises(sent)
        print("\n   %s: %d napi naplo-bejegyzes, %d Telegram-valasz (%d atirat), "
              "%d kuldott agens-uzenet, %d igeret-gyanus mondat"
              % (n.upper(), len(log), len(tg), len(files), len(sent), len(hits)))
        for e in log[:cap]:
            first = (e.get("content") or "").strip().splitlines()
            print("     naplo: %s" % (first[0][:160] if first else ""))
        for hm, x in sorted(hits)[:cap]:
            print("     %s  %s" % (hm, x))
        if len(hits) > cap:
            print("     ... es meg %d" % (len(hits) - cap))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.date.today().isoformat())
    a = ap.parse_args()

    dec = decisions_today(a.date)
    files, msgs = outgoing_today(a.date)
    hits = promises(msgs)

    print("NAPI IGERETEK %s -- atnezve: %d atirat-fajl, %d kimeno uzenet"
          % (a.date, len(files), len(msgs)))
    if not files or not msgs:
        print("FIGYELEM: nulla fajl vagy nulla kimeno uzenet -- ez valoszinubben a MERES hibaja, mint csendes nap.")
    print("\n1. MAI DONTESEK a docs/GAZDA-DONTESEK.md-ben: %d" % len(dec))
    for n, h in dec:
        print("   sor %d: %s" % (n, h[:200]))
    print("\n2. IGERET-GYANUS MONDATOK a mai kimeno uzenetekben: %d" % len(hits))
    for hm, s in hits:
        print("   %s  %s" % (hm, s))
    # The owner's follow-up, msg 4145: "vagy hogy a csapattagok mit csinaltak aznap?"
    print("\n3. A CSAPAT MAI NAPJA (ami kartya nelkul tortent vagy igerodott):")
    team_today(a.date)
    return 0


if __name__ == "__main__":
    sys.exit(main())
