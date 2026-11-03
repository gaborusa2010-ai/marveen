#!/usr/bin/env python3
"""Stalled mid-task: the agent that started working and then stopped.

Why this exists
---------------
2026-11-03: cody was on #777 (in_progress) when the model's safeguards cut his
turn at 07:58 and again at 09:04. The pane sat on an idle prompt until ~12:40
and nobody noticed until the owner asked "Hogy haladtok?" (tg 10535). The owner,
tg 10542: "Figyelj jobban oda a csapattagokra ... Lehet kesziteni egy riaszto
programot ami szol ha nem megy valamelyik agent?"

idle-agent-guard.py could not catch it: it alerts on ~zero tokens for the WHOLE
day, and cody had spent tokens before he stalled. This guard looks at the pane
itself, every few minutes.

Signal (all measured from the live tmux pane, session agent-<name>):
  - the agent is running (/api/agents) and its pane is NOT busy
    ("esc to interrupt" absent), AND
  - it has work it should be doing: an in_progress card, or an inbox message
    still `pending` after PENDING_MIN minutes, AND
  - the pane has not changed for ERROR_MIN minutes when it shows an
    interruption / API error line, STALL_MIN with an unread message, IDLE_MIN
    with only an in_progress card (standing cards exist; main agent only).

Escalation, once per stall episode (an episode ends when the pane changes):
  1. inter-agent message to marveen (owner msg 6085: findings go to the main
     agent, who acts);
  2. if it is an error or unread-message stall and the same episode is still stalled OWNER_AFTER_MIN minutes later, one
     Telegram line to the owner via scripts/notify.sh -- this is the part the
     owner asked for in tg 10542: if the main agent does not act, he hears it.

It never starts, stops or messages the stalled agent itself. It reports.

  stalled-agent-guard.py              # measure, update state, print JSON
  stalled-agent-guard.py --notify     # also send the alerts
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(REPO, "store")
STATE = os.path.join(STORE, "stalled-agent-guard.json")
TOKEN_FILE = os.path.join(STORE, ".dashboard-token")
API = "http://localhost:%s" % (os.environ.get("MARVEEN_WEB_PORT") or "3420")
MAIN = "marveen"

STALL_MIN = 30        # idle + unchanged pane with work waiting
ERROR_MIN = 10        # same, but the pane shows an interruption / API error
PENDING_MIN = 20      # an inbox message this old, still pending, counts as work
IDLE_MIN = 60         # idle with only an in_progress card (standing cards exist, e.g.
                      # "ALLO FELADAT", "Folyamatos ..."): main agent only, never the owner
OWNER_AFTER_MIN = 45  # still stalled this long after the main agent was told
OWNER_HOURS = range(7, 23)  # local hours when the owner may get a Telegram line

BUSY_RE = re.compile(r"esc to interrupt")
ERROR_RE = re.compile(r"safeguards stopped|API Error|Interrupted|rate.limit|"
                      r"usage limit|Not logged in|Credit balance", re.I)


def api_get(path):
    """Raises on anything but 200 + JSON: a meter that cannot fail cannot say no."""
    with open(TOKEN_FILE, encoding="utf-8") as fh:
        tok = fh.read().strip()
    req = urllib.request.Request(API + path, headers={"Authorization": "Bearer " + tok})
    with urllib.request.urlopen(req, timeout=15) as r:
        if r.status != 200:
            raise RuntimeError("%s -> HTTP %s" % (path, r.status))
        return json.loads(r.read().decode("utf-8"))


def running_agents():
    rows = api_get("/api/agents")
    if not isinstance(rows, list):
        raise RuntimeError("/api/agents: not a list")
    return [r["name"] for r in rows if r.get("running") and r["name"] != MAIN]


def in_progress_cards():
    """{name: ["#seq title", ...]}. GET /api/kanban takes no query params."""
    rows = api_get("/api/kanban")
    if not isinstance(rows, list):
        raise RuntimeError("/api/kanban: not a list")
    out = {}
    for c in rows:
        who = (c.get("assignee") or "").strip().lower()
        if c.get("status") == "in_progress" and not c.get("archived_at"):
            out.setdefault(who, []).append("#%s %s" % (c.get("seq", "?"), (c.get("title") or "")[:50]))
    return out


def old_pending(name, now):
    """Ids of messages TO name still pending after PENDING_MIN minutes."""
    rows = api_get("/api/messages?agent=%s&limit=50" % name)
    if not isinstance(rows, list):
        raise RuntimeError("/api/messages: not a list")
    out = []
    for m in rows:
        ts = m.get("created_at")
        if (m.get("to_agent") == name and m.get("status") == "pending"
                and isinstance(ts, (int, float)) and now - ts >= PENDING_MIN * 60):
            out.append(m["id"])
    # Worksource agents: the router marks the row `delivered` as soon as it is
    # queued (message-router.ts, "WHAT 'delivered' MEANS HERE"), so the DB never
    # shows them pending. The unacknowledged item sits in pending/ or active/
    # of the queue dir instead (layout: src/web/worksource-queue.ts). Roxy
    # measured the DB-only version dead on all 6 agents, #784 comment 3724.
    root = os.path.join(os.path.expanduser("~"), ".claude", "channels", "worksource", name)
    for stage in ("pending", "active"):
        d = os.path.join(root, stage)
        try:
            files = os.listdir(d)
        except OSError:
            continue
        for f in files:
            try:
                age = now - os.path.getmtime(os.path.join(d, f))
            except OSError:
                continue
            if f.endswith(".json") and age >= PENDING_MIN * 60:
                out.append(f[:-5])
    return out


def session_of(name):
    """tmux session: agent-<name>; the main agent lives in marveen-channels."""
    return "marveen-channels" if name == MAIN else "agent-" + name


def pane(name):
    """Last 40 lines of the agent's pane, or None if there is no session."""
    try:
        r = subprocess.run(["tmux", "capture-pane", "-p", "-t", session_of(name)],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode != 0:
        return None
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    return "\n".join(lines[-40:])


def assess(prev, text, now, cards, pending):
    """Pure decision for one agent. Returns (new_state, stalled, reason)."""
    h = hashlib.sha256(text.encode("utf-8")).hexdigest()
    st = dict(prev or {})
    if st.get("hash") != h:
        st = {"hash": h, "since": now}          # pane moved: new episode
    busy = bool(BUSY_RE.search(text))
    tail = "\n".join(text.splitlines()[-15:])
    err = ERROR_RE.search(tail)
    unchanged_min = (now - st["since"]) / 60.0
    limit = ERROR_MIN if err else STALL_MIN
    has_work = bool(cards) or bool(pending)
    # An empty capture (session just starting) shows nothing to judge.
    stalled = bool(text.strip()) and (not busy) and has_work and unchanged_min >= limit
    if stalled and not err and not pending and unchanged_min < IDLE_MIN:
        stalled = False    # plain idle with a card: could be a standing card, wait longer
    st["owner_worthy"] = bool(stalled and (err or pending))
    reason = ""
    if stalled:
        parts = ["%d perce all" % unchanged_min]
        if err:
            parts.append("hiba a paneon: %r" % err.group(0))
        if cards:
            parts.append("folyamatban: " + "; ".join(cards[:3]))
        if pending:
            parts.append("olvasatlan uzenet: " + ", ".join(map(str, pending[:5])))
        reason = ", ".join(parts)
    return st, stalled, reason


def send_to_main(text):
    """Delivered ONLY if agent-msg.sh printed `OK id=`."""
    try:
        r = subprocess.run(["bash", os.path.join(REPO, "scripts", "agent-msg.sh"), MAIN, MAIN, text],
                           capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.stdout.strip().startswith("OK id=")


def send_to_owner(text):
    try:
        r = subprocess.run(["bash", os.path.join(REPO, "scripts", "notify.sh"), text],
                           capture_output=True, text=True, timeout=90)
    except (OSError, subprocess.SubprocessError):
        return False
    return r.returncode == 0


def load():
    try:
        with open(STATE, encoding="utf-8") as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def save(d):
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--notify", action="store_true", help="send the alerts (default: measure only)")
    a = ap.parse_args(argv)

    now = time.time()
    state = load()
    cards = in_progress_cards()
    rows, new_state = [], {}
    # The main agent is watched too (Roxy, #784 comment 3724): if I stall, nobody
    # else reads my inbox. My own alert goes straight to the owner, and only for
    # an error or unread-message stall -- my in_progress cards are long-running.
    for name in [MAIN] + running_agents():
        text = pane(name)
        if text is None:
            rows.append({"agent": name, "error": "no tmux session %s" % session_of(name)})
            continue
        pend = old_pending(name, now)
        st, stalled, reason = assess(state.get(name), text, now,
                                     [] if name == MAIN else cards.get(name, []), pend)
        if not stalled:
            st.pop("main_at", None)
            st.pop("owner_at", None)
        if stalled and a.notify and name == MAIN:
            if ("owner_at" not in st and st.get("owner_worthy")
                    and time.localtime(now).tm_hour in OWNER_HOURS):
                if send_to_owner("Riasztas: Marveen elakadt: %s." % reason):
                    st["owner_at"] = now
        elif stalled and a.notify:
            if "main_at" not in st:
                if send_to_main("[ELAKADT AGENS] %s: %s. Nezd meg a paneljat "
                                "(tmux capture-pane -p -t %s) es inditsd ujra a munkat. "
                                "%s" % (name, reason, session_of(name),
                                        "Ha %d percen belul nem mozdul, a gazda Telegramon kap riasztast."
                                        % OWNER_AFTER_MIN if st.get("owner_worthy") else
                                        "Csak folyamatban levo kartya, hiba nelkul: lehet allo kartya, a gazdat nem riasztja.")):
                    st["main_at"] = now
            elif (st.get("owner_worthy") and "owner_at" not in st and now - st["main_at"] >= OWNER_AFTER_MIN * 60
                  and time.localtime(now).tm_hour in OWNER_HOURS):
                if send_to_owner("Riasztas: %s elakadt, es Marveen %d perce nem inditotta "
                                 "ujra. Reszletek: %s." % (name.capitalize(), OWNER_AFTER_MIN, reason)):
                    st["owner_at"] = now
        new_state[name] = st
        rows.append({"agent": name, "stalled": stalled, "reason": reason,
                     "unchanged_min": round((now - st["since"]) / 60, 1),
                     "main_told": "main_at" in st, "owner_told": "owner_at" in st})
    save(new_state)
    print(json.dumps({"rows": rows}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
