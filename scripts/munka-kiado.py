#!/usr/bin/env python3
"""Work dispatcher: every running agent with nothing in progress gets its next card.

Why this exists
---------------
2026-11-05, owner tg 10751/10753/10756: Cody ran all day with 8 unblocked
planned cards and did nothing. idle-agent-guard.py saw it at 09:00 and told
the main agent; the main agent closed the alert without handing over work.
A report that needs a human-like step to act on it can be dropped. The owner:
"irjunk egy programot, ami kiadja a munkat minden ugynoknek?! Meg neked is?!"

What it does
------------
For each agent the dashboard lists as RUNNING (marveen included):
  - if it has a card in `in_progress`, it is busy: nothing happens;
  - otherwise its best startable `planned` card is moved to `in_progress`.
The move itself is the handover: the kanban route (fireKanbanDispatch in
src/web/routes/kanban.ts) wakes the assignee once, through the normal
inter-agent queue, the main agent too.

Startable = planned, assigned to the agent, not archived, every blocker done,
start_date not in the future, and not set aside by the owner (FELRETE...,
KESOBBRE, "set aside", ... in title or description: those cards wait on the
owner's word even when their status says planned).

Order: priority (urgent, high, normal, low), then the board's sort_order,
then seq.

What it does NOT do
-------------------
It never starts a stopped agent (owner msg 2944: only agents that run, tokens).
It never touches waiting/testing cards. At most one card per agent per run,
and a card it already dispatched today is not dispatched again (an agent that
puts a card back to planned has a reason; the next run picks the next card,
not the same one).
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STORE = os.path.join(REPO, "store")
STATE = os.path.join(STORE, "munka-kiado.json")
TOKEN_FILE = os.path.join(STORE, ".dashboard-token")
API = "http://localhost:%s" % (os.environ.get("MARVEEN_WEB_PORT") or "3420")
ACTOR = "munka-kiado"
PRIO = {"urgent": 0, "high": 1, "normal": 2, "low": 3}

# Owner's set-aside markers, as they appear on the board (accent-free and
# accented spellings both). Matching is case-insensitive.
SET_ASIDE = re.compile(
    # FELTETELES / CSAK AMIKOR: conditional cards (#81 "ha a harom konyv bejon",
    # #250 "csak amikor nincs konyv-munka"): the 11-05 first run handed #250 to
    # Roxy and she had to put it back.
    r"FELRET|F[EÉ]LRET|K[EÉ]S[OŐ]BBRE|SET ASIDE|PARKOLVA|NE KEZDD|ON HOLD"
    r"|FELT[EÉ]TELES|CSAK AMIKOR|CONDITIONAL",
    re.IGNORECASE,
)


def _token():
    with open(TOKEN_FILE, encoding="utf-8") as fh:
        return fh.read().strip()


def api(method, path, body=None):
    """Raises on anything but 2xx + JSON: an error must not read as 'no work'."""
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={
        "Authorization": "Bearer " + _token(),
        "Content-Type": "application/json",
        "X-Agent-Id": ACTOR,
    })
    with urllib.request.urlopen(req, timeout=15) as r:
        if not 200 <= r.status < 300:
            raise RuntimeError("%s %s -> HTTP %s" % (method, path, r.status))
        return json.loads(r.read().decode("utf-8"))


def is_set_aside(card):
    text = "%s\n%s" % (card.get("title") or "", card.get("description") or "")
    return bool(SET_ASIDE.search(text))


def startable(card, now_ts):
    if card.get("status") != "planned" or card.get("archived_at"):
        return False
    for b in card.get("blockers") or []:
        if b.get("status") != "done":
            return False
    sd = card.get("start_date")
    if isinstance(sd, (int, float)) and sd > now_ts:
        return False
    if isinstance(sd, str) and sd[:10] > datetime.date.fromtimestamp(now_ts).isoformat():
        return False
    return not is_set_aside(card)


def sort_key(card):
    return (PRIO.get(card.get("priority"), 9), card.get("sort_order") or 0, card.get("seq") or 0)


def plan(agents, cards, now_ts, already_today):
    """Pure decision: {agent: card or None, ...} plus a reason per agent.

    agents: {name: running bool}; cards: board list; already_today: set of card ids.
    """
    out = {}
    for name, running in sorted(agents.items()):
        mine = [c for c in cards if (c.get("assignee") or "").strip().lower() == name
                and not c.get("archived_at")]
        if not running:
            has_work = any(startable(c, now_ts) or c.get("status") == "in_progress" for c in mine)
            out[name] = (None, "nem fut, de van munkaja" if has_work else "nem fut")
            continue
        if any(c.get("status") == "in_progress" for c in mine):
            out[name] = (None, "van folyamatban levo kartyaja")
            continue
        cand = sorted((c for c in mine if startable(c, now_ts) and c["id"] not in already_today),
                      key=sort_key)
        if not cand:
            out[name] = (None, "nincs elkezdheto kartyaja")
            continue
        out[name] = (cand[0], "kiadva")
    return out


def _journal(unit, since="today"):
    """Message lines of a user unit's journal; [] when journalctl fails or is absent."""
    try:
        r = subprocess.run(["journalctl", "--user", "-u", unit, "--since", since,
                            "--no-pager", "-o", "short-iso"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return []
    return r.stdout.splitlines() if r.returncode == 0 else []


def _hhmm(iso_line):
    # short-iso line: 2026-11-05T17:50:00+00:00 host proc[pid]: message
    m = re.match(r"\d{4}-\d\d-\d\dT(\d\d:\d\d)", iso_line)
    return m.group(1) if m else "?"


LATER_TODAY = "ma kesobb indul (menetrend szerint): "


def why_not_running(name, budget, today_start_ts):
    """Owner tg 10758: an agent with work that does not run gets a reason.

    Sources, newest decision wins (each is a measurement from a log or state
    file, never a guess; when none speaks, the answer says so):
      - agent-idle-stop journal: "<name> LEALLITVA: <why>"
      - agent-budget-guard.json: stopped_at today = token cap
      - the agent's day-start unit: "NINCS munka ... nem inditom", or started
    """
    found = []  # (sort key, text)
    for line in _journal("marveen-agent-idle-stop.service"):
        msg = line.split(": ", 1)[-1]
        if msg.startswith(name + " ") and "LEALLITVA" in msg:
            found.append((_hhmm(line), "%s-kor leallitotta a tetlenseg-figyelo: %s"
                          % (_hhmm(line), msg.split("LEALLITVA:", 1)[-1].split("|")[0].strip())))
    b = budget.get(name) or {}
    st = b.get("stopped_at")
    if isinstance(st, (int, float)) and st >= today_start_ts:
        hm = datetime.datetime.fromtimestamp(st).strftime("%H:%M")
        found.append((hm, "%s-kor leallitotta a token-keret (%sM / %sM)"
                      % (hm, round((b.get("tokens") or 0) / 1e6), round((b.get("cap") or 0) / 1e6))))
    started = None
    for line in _journal("marveen-%s-day-start.service" % name):
        msg = line.split(": ", 1)[-1]
        if "nem inditom" in msg:
            # The unit logs "HH:MM:SS <name>: NINCS munka ... nem inditom"; take the
            # text after "<name>: ", not after the first ':' (that one is the clock).
            reason = msg.split(name + ": ", 1)[-1] if (name + ": ") in msg else msg
            found.append((_hhmm(line), "%s-kor a reggeli indito nem inditotta: %s"
                          % (_hhmm(line), reason.strip())))
        elif "runState=running" in msg:
            started = _hhmm(line)
    nxt = _next_start(name)
    tail = ("; kovetkezo inditas: %s" % nxt) if nxt else "; nincs inditasi idozitoje"
    if found:
        return sorted(found)[-1][1] + tail
    if not started and nxt and nxt.startswith(datetime.date.today().isoformat()):
        # Night-shift agents (anorak 23:00): not running yet is the schedule,
        # not a fault; shown in --dry-run, never reported.
        return LATER_TODAY + nxt
    if started:
        return ("%s-kor elindult, de a leallas oka egyik naplobol sem latszik "
                "(lehet osszeomlas vagy kezi leallitas)" % started) + tail
    return "ma nem indult, es a naplok nem mondjak meg, miert" + tail


def _next_start(name):
    """Next run of the agent's own start timer(s) (day-start, shift-start)."""
    try:
        r = subprocess.run(["systemctl", "--user", "list-timers", "--all", "--no-pager"],
                           capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired):
        return None
    for line in r.stdout.splitlines():
        unit = next((w for w in line.split() if w.startswith("marveen-%s-" % name)
                     and w.endswith(".timer") and "start" in w and "watch" not in w), None)
        if unit:
            m = re.search(r"\w{3} (\d{4}-\d\d-\d\d \d\d:\d\d)", line)
            return "%s (%s)" % (m.group(1) if m else "?", unit)
    return None


def load_state(today):
    try:
        with open(STATE, encoding="utf-8") as fh:
            d = json.load(fh)
    except (OSError, ValueError):
        d = {}
    if not isinstance(d, dict):
        d = {}
    return {today: d.get(today, []), "reported_" + today: d.get("reported_" + today, [])}


def save_state(d):
    os.makedirs(STORE, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(d, fh, ensure_ascii=False)
    os.replace(tmp, STATE)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--dry-run", action="store_true", help="csak kiirja, mit adna ki")
    args = ap.parse_args(argv)

    now = datetime.datetime.now()
    today = now.strftime("%Y-%m-%d")
    agents_rows = api("GET", "/api/agents")
    if not isinstance(agents_rows, list):
        raise RuntimeError("/api/agents: nem lista")
    agents = {r["name"].strip().lower(): bool(r.get("running")) for r in agents_rows}
    # The main agent is not always in /api/agents; this script runs only while
    # the dashboard runs, and the main session is the one that owns it.
    agents.setdefault("marveen", True)
    cards = api("GET", "/api/kanban")
    if not isinstance(cards, list):
        raise RuntimeError("/api/kanban: nem lista")

    try:
        with open(os.path.join(STORE, "agent-budget-guard.json"), encoding="utf-8") as fh:
            budget = json.load(fh)
    except (OSError, ValueError):
        budget = {}
    today_start = datetime.datetime.combine(now.date(), datetime.time()).timestamp()
    state = load_state(today)
    decisions = plan(agents, cards, now.timestamp(), set(state[today]))
    rows = []
    for name, (card, why) in decisions.items():
        row = {"agent": name, "result": why}
        if card:
            row.update(card_seq=card.get("seq"), card_id=card["id"], title=(card.get("title") or "")[:80])
            if not args.dry_run:
                try:
                    api("POST", "/api/kanban/%s/move" % card["id"],
                        {"status": "in_progress", "actor": ACTOR, "expected_status": "planned"})
                    state[today].append(card["id"])
                except (urllib.error.URLError, RuntimeError) as e:
                    row["result"] = "HIBA: %s" % e
        if why == "nem fut, de van munkaja":
            row["miert_nem_fut"] = why_not_running(name, budget, today_start)
        rows.append(row)

    # One report per agent per day to the main agent's inbox: it decides
    # whether to start the agent (this script never starts anyone).
    rep_key = "reported_" + today
    fresh = [r for r in rows if r.get("miert_nem_fut") and r["agent"] not in state[rep_key]
             and not r["miert_nem_fut"].startswith(LATER_TODAY)]
    if fresh and not args.dry_run:
        lines = ["[MUNKA-KIADO] Nem fut, pedig van munkaja (gazda tg 10758):"]
        lines += ["- %s: %s" % (r["agent"], r["miert_nem_fut"]) for r in fresh]
        lines.append("TEENDO (marveen): ha az ok nem szandekos (osszeomlas, hibas leallitas), "
                     "inditsd el; ha szandekos (keret, pihenonap), hagyd.")
        try:
            r = subprocess.run(["bash", os.path.join(REPO, "scripts", "agent-msg.sh"),
                                ACTOR, "marveen", "\n".join(lines)],
                               capture_output=True, text=True, timeout=30)
            if r.stdout.strip().startswith("OK id="):
                state[rep_key] += [x["agent"] for x in fresh]
            else:
                sys.stderr.write("munka-kiado: jelentes nem ment ki: %s %s\n" % (r.stdout, r.stderr))
        except (OSError, subprocess.TimeoutExpired) as e:
            sys.stderr.write("munka-kiado: jelentes hiba: %s\n" % e)
    if not args.dry_run:
        save_state(state)
    print(json.dumps({"at": now.strftime("%Y-%m-%d %H:%M"), "dry_run": args.dry_run, "rows": rows},
                     ensure_ascii=False))
    return 1 if any(r["result"].startswith("HIBA") for r in rows) else 0


if __name__ == "__main__":
    sys.exit(main())
