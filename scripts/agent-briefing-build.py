#!/usr/bin/env python3
"""Build a start-of-shift briefing for an agent from its OWN open work.

WHY IT IS GENERATED AND NOT HAND-WRITTEN
----------------------------------------
agent-night-shift.sh already delivers store/briefing-<agent>.txt the moment a
start is confirmed, and already reports when the file is MISSING. Both halves
worked. What did not work is the half in between: somebody has to write the
file, and on 2026-09-23 08:00:37 nobody had -- the journal line reads
"cody NINCS eligazitas-fajl ... ures prompton indul". The agent was woken up
and nobody spoke to it.

A missing-file alarm cannot fix that on its own: it fires at start time, when
the person who should have written the file is asleep. So the briefing is built
from the two sources that always exist and are always current -- the agent's
open kanban cards and its undelivered/unfinished inbox.

WHAT IT WILL NOT DO
-------------------
1. It NEVER overwrites an existing briefing file. A hand-written handover is
   always better than a generated list, and the generated one must not be able
   to eat it. Existing file -> exit 0, print, change nothing.
2. It NEVER invents work. If the agent has no open card and no open message,
   the briefing says exactly that and tells the agent to ASK rather than find
   itself something to do -- an agent that invents a task spends the owner's
   quota on it. In that case the main agent is notified too, because assigning
   work is the main agent's job, not the sleeping agent's.

Usage
-----
  agent-briefing-build.py cody                 # write store/briefing-cody.txt
  agent-briefing-build.py --all                # every sub-agent
  agent-briefing-build.py cody --dry-run       # print, write nothing
  agent-briefing-build.py cody --force         # overwrite an existing file
"""
import argparse
import hashlib
import json
import os
import sys
import urllib.request
from datetime import datetime

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(REPO, "store")
DASH = os.environ.get("MARVEEN_DASHBOARD_URL", "http://localhost:3420")
SUBS = ("anorak", "cody", "forgacs", "margo", "roxy")
MAIN = "marveen"
OPEN_CARD_STATUSES = ("planned", "in_progress", "testing", "waiting")
# A receipt is not work. The drain that surfaces real questions already handles
# those; listing them here would bury the two cards that matter under twenty
# "[Eredmeny] msg_id:N status:done" lines.
RECEIPT_PREFIXES = ("[Eredmeny]", "[Eredmény]")
# #663, owner tg 9195-9198: the sheet listed [CONTEXT-GUARD] and [kanban-kovetkezo]
# notices as open work. They are about the agent's own session or about a card
# that is already on the sheet, so they are dropped here; they stay in the inbox.
SYSTEM_SENDERS = ("system",)
# #663, owner tg 9197: until 10-25 the sheet carried only the card TITLE, cut at
# 90 characters. Measured 06:00-07:00 that morning: cody 36 tool calls, roxy 4,
# forgacs 3, none opened a card; margo opened one. So the agents worked from the
# title and their memory. The head of the description and the last comment now
# ride along for every card the agent can work on.
DESC_HEAD = 300
COMMENT_HEAD = 200
WAIT_KIND_LABEL = {"owner": "a gazdara", "date": "datumra", "card": "masik kartyara",
                   "external": "kulso felre", "agent": "masik agensre"}
WORKABLE = ("in_progress", "testing", "planned")
REVIEW_STAMPS = ("[Heti átnézés]", "[Heti atnezes]")
# How many open rows a briefing may list. See the comment at the cap itself.
MESSAGE_CAP = 8
SKILL_CMD = "python3 /home/gabor/marveen/scripts/skill-valasztas.py"


def _token():
    with open(os.path.join(STORE, ".dashboard-token")) as f:
        return f.read().strip()


def _get(path, tok):
    req = urllib.request.Request(DASH + path, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode())


def _rows(payload, *keys):
    if isinstance(payload, list):
        return payload
    for k in keys:
        if isinstance(payload.get(k), list):
            return payload[k]
    return []


def open_cards(tok, agent):
    # GET /api/kanban takes NO query parameters (measured; a filter here is a
    # 400, not a narrower list), so the filtering happens on this side.
    cards = _rows(_get("/api/kanban", tok), "cards", "items")
    mine = [c for c in cards
            if (c.get("assignee") or "").lower() == agent.lower()
            and c.get("status") in OPEN_CARD_STATUSES
            and not c.get("archived_at")]
    order = {s: i for i, s in enumerate(("in_progress", "testing", "waiting", "planned"))}
    prio = {p: i for i, p in enumerate(("urgent", "high", "normal", "low"))}
    mine.sort(key=lambda c: (order.get(c.get("status"), 9),
                             prio.get(c.get("priority"), 9),
                             c.get("seq") or 0))
    return mine


def open_messages(tok, agent):
    msgs = _rows(_get("/api/messages?agent=%s&limit=200" % agent, tok), "messages", "items")
    out = []
    for m in msgs:
        if m.get("to_agent") != agent:
            continue
        if m.get("from_agent") in SYSTEM_SENDERS:
            continue
        if m.get("status") in ("done", "failed"):
            continue
        content = (m.get("content") or "").lstrip()
        if content.startswith(RECEIPT_PREFIXES):
            continue
        out.append(m)
    out.sort(key=lambda m: m.get("id") or 0)
    return out


def last_comments(tok, cards):
    """Last comment per workable card, keyed by card id. A human (agent) comment
    beats an automated one; a card whose comments cannot be fetched simply gets
    none -- the briefing must still go out."""
    out = {}
    for c in cards:
        if c.get("status") not in WORKABLE:
            continue
        try:
            rows = _rows(_get("/api/kanban/%s/comments" % c.get("id"), tok), "comments", "items")
        except Exception as e:
            sys.stderr.write("#%s: a megjegyzesek NEM jottek le: %s\n" % (c.get("seq"), e))
            continue
        rows = sorted(rows, key=lambda r: (r.get("created_at") or 0, r.get("id") or 0))
        # The weekly review stamps "[Heti átnézés] ... marad" on every kept card; on
        # roxy's 10-25 sheet it was the "last comment" of three cards out of five and
        # said nothing about the work. It is used only when nothing else exists.
        human = [r for r in rows if not r.get("automated")
                 and not (r.get("content") or "").lstrip().startswith(REVIEW_STAMPS)]
        if human or rows:
            out[c.get("id")] = (human or rows)[-1]
    return out


def _flat(text, n):
    t = " ".join((text or "").split())
    return t if len(t) <= n else t[:n].rstrip() + "..."


def agent_waits(tok, agent):
    """Cards where this agent waits on another agent, and cards where others wait
    on it, from scripts/masikra-var.py (owner tg 9069: #457/#583 sat six days on a
    free-text "Marveen review" wait that nothing could fire on). Only the rows that
    need an action: no ETA yet, or an ETA that has passed; every wait on this agent."""
    import importlib.util
    import time
    spec = importlib.util.spec_from_file_location(
        "masikra_var", os.path.join(REPO, "scripts", "masikra-var.py"))
    mv = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mv)
    cards = _rows(_get("/api/kanban", tok), "cards", "items")
    agents = sorted({(a.get("name") or "").lower() for a in _rows(_get("/api/agents", tok), "agents")} | {MAIN})
    mine, on_me = mv.classify(cards, agents, agent, int(time.time()))
    return [r for r in mine if not r["state"].startswith("ETA ") or "LEJART" in r["state"]], on_me


def digest(cards, msgs):
    """Fingerprint of what the briefing is ABOUT, not of its text: the card set
    with each card's updated_at (a description edit or a comment moves it), and
    the open message ids. The date header is deliberately not in it."""
    key = [[c.get("id"), c.get("status"), c.get("priority"), c.get("title"),
            c.get("updated_at")] for c in cards]
    key.append(sorted(m.get("id") for m in msgs))
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()[:16]


def render(agent, cards, msgs, now=None, unchanged_since=None, waits=None, comments=None):
    now = now or datetime.now()
    L = ["[ELIGAZITAS %s -- gepi, a sajat nyitott munkadbol]" % now.strftime("%Y-%m-%d %H:%M"),
         ""]
    # #663, owner tg 9180: the order is cards and new tasks -> skill choice and
    # loading -> work. Until 10-25 the skill choice came at 07:35, after an hour
    # and a half of work done with yesterday's skills.
    L += ["SORREND (gazda tg 9180):",
          "  1. Olvasd el az alabbi kartyaidat es uzeneteidet (a kozben jott uj feladat is uzenet).",
          "  2. Valassz skillt a mai munkahoz: %s lista %s, aztan valaszt %s <skill>..."
          % (SKILL_CMD, agent, agent),
          "     A kivalasztottat a Skill toollal toltsd be, mielott hozzakezdesz.",
          "  3. Utana a munka, prioritas szerint.",
          ""]
    comments = comments or {}
    work = [c for c in cards if c.get("status") in WORKABLE]
    parked = [c for c in cards if c.get("status") not in WORKABLE]
    if work:
        L.append("DOLGOZHATO KARTYAID (%d). A SORSZAMOT hasznald, ha rajuk hivatkozol." % len(work))
        L.append("A leiras eleje es az utolso megjegyzes itt van; a teljes kartya:")
        L.append("  GET /api/kanban/<id> es /api/kanban/<id>/comments")
        for c in work:
            L.append("  #%s  %-11s %-6s  %s  (id %s)" % (c.get("seq"), c.get("status"),
                                                       c.get("priority"), _flat(c.get("title"), 120),
                                                       c.get("id")))
            if (c.get("description") or "").strip():
                L.append("      Leiras: " + _flat(c.get("description"), DESC_HEAD))
            cm = comments.get(c.get("id"))
            if cm:
                when = datetime.fromtimestamp(cm["created_at"]).strftime("%m-%d %H:%M") \
                    if isinstance(cm.get("created_at"), (int, float)) else "?"
                L.append("      Utolso megjegyzes (%s, %s): %s"
                         % (cm.get("author") or "?", when, _flat(cm.get("content"), COMMENT_HEAD)))
        L.append("")
    if parked:
        L.append("VARO KARTYAID (%d): addig nincs veluk teendod, amig az ok fennall." % len(parked))
        for c in parked:
            kind = WAIT_KIND_LABEL.get(c.get("wait_kind"), c.get("wait_kind"))
            why = _flat(c.get("wait_note"), 110)
            if isinstance(c.get("wait_until"), (int, float)):
                why = ("%s-ig; " % datetime.fromtimestamp(c["wait_until"]).strftime("%m-%d")) + why
            if kind or why:
                L.append("  #%s  %s: var %s%s" % (c.get("seq"), _flat(c.get("title"), 60),
                                                  kind or "", (" -- " + why) if why else ""))
            else:
                L.append("  #%s  %s: NINCS OK a kartyan -> ird ra, mire var"
                         % (c.get("seq"), _flat(c.get("title"), 60)))
        L.append("")
    if waits and (waits[0] or waits[1]):
        mine, on_me = waits
        L.append("MASIK AGENSRE VARAS (skill masik-agensre-var, gazda msg 9069):")
        for r in mine:
            L.append("  #%s  te varsz ra: %s, %s -> kerdezd meg, mikor lesz kesz"
                     % (r["seq"], ",".join(r["waits_on"]), r["state"]))
        for r in on_me:
            L.append("  #%s  %s var RAD (%s, %.0f napja) -> valaszolj idoponttal, vagy csinald meg"
                     % (r["seq"], r["assignee"], r["state"], r["days"]))
        L.append("")
    if msgs:
        # CAP, and the reason is a measurement: cody carried 16 open rows on
        # 2026-09-23, most of them notices from five days earlier that only
        # needed closing. Sixteen lines every morning is a page of noise that
        # buries the two cards above it -- and it is paid for in tokens on every
        # single start. The newest MESSAGE_CAP are shown, the rest are counted.
        shown = msgs[-MESSAGE_CAP:]
        if len(msgs) > len(shown):
            L.append("LEZARATLAN UZENETEID (%d, a legujabb %d itt). Amelyiket elvegezted, ZARD LE:"
                     % (len(msgs), len(shown)))
            L.append("  (a maradek %d regebbi: GET /api/messages?agent=%s)" % (len(msgs) - len(shown), agent))
        else:
            L.append("LEZARATLAN UZENETEID (%d). Amelyiket elvegezted, ZARD LE:" % len(msgs))
        for m in shown:
            first = (m.get("content") or "").strip().split("\n")[0]
            L.append("  msg %s  <- %s: %s" % (m.get("id"), m.get("from_agent"), first[:100]))
        L.append("")
    if not cards and not msgs:
        L += [
            "NINCS KIOSZTOTT MUNKAD: nulla nyitott kartya, nulla lezaratlan uzenet.",
            "",
            "EZ NEM FELHIVAS ARRA, HOGY TALALJ MAGADNAK. Egy kitalalt feladat ugyanannyi",
            "keretet visz el, mint egy kert, csak nem kerte senki. Nezd at a sajat",
            "agents/%s/ mappadat (a legfrissebb riportot es tervet), es ha ott sincs" % agent,
            "folytatnivalo, KERDEZZ RA egy sorban Marveennel, es addig maradj csendben.",
            "",
        ]
    elif unchanged_since:
        # WHY: cody measured it (weekly self-check 2026-09-27): the 09-26 and 09-27
        # briefings were byte-identical apart from the date, and each one still cost
        # a full round of re-fetching, re-checking and closing. The agent is started
        # either way, so the briefing still goes out; it just says there is nothing
        # new to re-verify.
        L += ["VALTOZATLAN a %s-i eligazitas ota: ugyanazok a kartyak, egyik sem mozdult," % unchanged_since,
              "es ugyanazok az uzenetek. NEM kell ujra atnezni, visszamerni vagy nyugtazni:",
              "folytasd a munkat ott, ahol tartottal.", ""]
    else:
        L += ["Ha barmelyik tetel mar elavult, mondd meg, ne csendben hagyd ott.", ""]
    L.append("Ezt a lapot a scripts/agent-briefing-build.py allitotta ossze, nem ember.")
    L.append("Amit NEM tartalmaz: a mai hirek es a gazda elo kerései. Azokert nezd meg az")
    L.append("uzeneteidet indulas utan.")
    return "\n".join(L) + "\n"


def notify_main(agent, tok):
    """Tell the MAIN agent that this one has nothing queued -- assigning work is
    its job. Sent through the API rather than agent-msg.sh so this script has no
    shell-quoting surface at all."""
    body = json.dumps({
        "from": MAIN, "to": MAIN,
        "content": ("[NINCS MUNKA] %s a kovetkezo indulasara NULLA nyitott kartyaval es "
                    "NULLA lezaratlan uzenettel all. Az eligazitasa ezt mondja neki, es "
                    "azt, hogy ne talaljon ki maganak feladatot. Ha van mit atadni, most "
                    "tedd meg.\nEzt a sort a scripts/agent-briefing-build.py irja." % agent)
    }).encode()
    req = urllib.request.Request(DASH + "/api/messages", data=body, method="POST",
                                 headers={"Authorization": "Bearer " + tok,
                                          "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        # The house rule: a send counts only when an id comes back.
        return json.loads(r.read().decode()).get("id")


def build(agent, tok, force=False, dry=False, quiet_when_empty=False):
    path = os.path.join(STORE, "briefing-%s.txt" % agent)
    legacy = os.path.join(STORE, "night-briefing-%s.txt" % agent)
    for p in (path, legacy):
        if os.path.exists(p) and os.path.getsize(p) > 0 and not force:
            print("%s: MAR VAN eligazitas (%s), nem irom felul" % (agent, os.path.basename(p)))
            return 0
    cards = open_cards(tok, agent)
    msgs = open_messages(tok, agent)
    try:
        waits = agent_waits(tok, agent)
    except Exception as e:
        sys.stderr.write("%s: a varakozas-lista NEM keszult el: %s\n" % (agent, e))
        waits = None
    dg = digest(cards, msgs)
    dpath = os.path.join(STORE, "briefing-digest-%s.json" % agent)
    prev = {}
    try:
        with open(dpath) as f:
            prev = json.load(f)
    except Exception:
        pass
    same = (cards or msgs) and prev.get("digest") == dg
    text = render(agent, cards, msgs, unchanged_since=prev.get("since") if same else None,
                  waits=waits, comments=last_comments(tok, cards))
    if dry:
        print("--- %s (dry-run, %d kartya, %d uzenet) ---" % (agent, len(cards), len(msgs)))
        print(text)
        return 0
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    try:
        since = prev.get("since") if same else datetime.now().strftime("%Y-%m-%d")
        with open(dpath + ".tmp", "w") as f:
            json.dump({"digest": dg, "since": since}, f)
        os.replace(dpath + ".tmp", dpath)
    except Exception as e:
        sys.stderr.write("%s: a digest NEM irodott ki: %s\n" % (agent, e))
    print("%s: eligazitas megirva (%d kartya, %d uzenet) -> %s"
          % (agent, len(cards), len(msgs), path))
    if not cards and not msgs and not quiet_when_empty:
        try:
            mid = notify_main(agent, tok)
            print("%s: a fo-agens ertesitve, hogy nincs kiosztott munkaja (id=%s)" % (agent, mid))
        except Exception as e:
            sys.stderr.write("%s: a nincs-munka jelzes NEM ment ki: %s\n" % (agent, e))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("agent", nargs="?")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--quiet-when-empty", action="store_true",
                    help="ne ertesitsd a fo-agenst, ha nincs kiosztott munka")
    a = ap.parse_args(argv)
    if not a.all and not a.agent:
        ap.error("agent nev vagy --all kell")
    tok = _token()
    targets = SUBS if a.all else (a.agent,)
    rc = 0
    for t in targets:
        try:
            rc |= build(t, tok, force=a.force, dry=a.dry_run,
                        quiet_when_empty=a.quiet_when_empty)
        except Exception as e:
            sys.stderr.write("%s: HIBA: %s\n" % (t, e))
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
