#!/usr/bin/env python3
"""Cross-check: every card that moves to `done` is reviewed by ANOTHER agent.

Owner's idea (Telegram msg 8284/8287, 2026-10-18): idle agents cost nothing
while they stand, so give them useful work -- checking each other's finished
cards, so fewer mistakes reach the owner. Decision msg 8291: no upper limit.

Model-free dispatcher, run by a systemd user timer:
  1. reads new `-> done` transitions from kanban_card_events (read-only sqlite),
  2. picks a reviewer: a RUNNING agent, not the card's author, not the main
     agent, lowest load (this weekly window's tokens + reviews already handed
     out today), so the work spreads instead of piling on one agent,
  3. sends the reviewer a brief over /api/messages and checks that an id came
     back (a curl exit 0 is not a delivery).

The reviewer writes its verdict on the card as a comment ("[Kereszt-ellenorzes]
RENDBEN" or "... HIBA: ..."), tells the author when something is wrong, and
closes the message with notify:false.

First run sets the baseline at the newest event: the backlog is NOT flooded.
State: store/kereszt-ellenorzes.json.

  kereszt-ellenorzes.py            # dispatch new done cards
  kereszt-ellenorzes.py --dry-run  # print what would be sent, send nothing
"""
import argparse
import datetime
import glob
import json
import os
import sqlite3
import sys
import time
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = "http://localhost:%s" % os.environ.get("MARVEEN_WEB_PORT", "3420")
DB = os.path.join(ROOT, "store", "claudeclaw.db")
STATE = os.path.join(ROOT, "store", "kereszt-ellenorzes.json")
TOKEN = os.path.join(ROOT, "store", ".dashboard-token")
MAIN = "marveen"
OWNER_NAMES = {"fejes istvan gabor", "gazda", "owner"}
# One review counted as this many tokens of load when spreading the work.
REVIEW_WEIGHT = 50_000_000


def _token():
    with open(TOKEN) as f:
        return f.read().strip()


def api(method, path, body=None):
    data = None if body is None else json.dumps(body, ensure_ascii=False).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + _token())
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=20) as r:
        return json.loads(r.read().decode() or "null")


def load_state():
    try:
        with open(STATE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_state(st):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f, ensure_ascii=False, indent=1)
    os.replace(tmp, STATE)


def db():
    return sqlite3.connect("file:%s?mode=ro" % DB, uri=True)


def done_events(after_id):
    with db() as c:
        return c.execute(
            "SELECT e.id, e.card_id, e.actor, e.created_at, k.rowid, k.title, k.assignee, "
            "COALESCE(k.description, ''), k.status "
            "FROM kanban_card_events e JOIN kanban_cards k ON k.id = e.card_id "
            "WHERE e.to_status = 'done' AND e.id > ? ORDER BY e.id", (after_id,)).fetchall()


def max_event_id():
    with db() as c:
        return c.execute("SELECT COALESCE(MAX(id), 0) FROM kanban_card_events").fetchone()[0]


def last_comments(card_id, n=4, width=700):
    with db() as c:
        rows = c.execute(
            "SELECT author, content FROM kanban_comments WHERE card_id = ? "
            "ORDER BY created_at DESC, id DESC LIMIT ?", (card_id, n)).fetchall()
    out = []
    for author, content in reversed(rows):
        text = " ".join(str(content or "").split())
        out.append("- %s: %s" % (author, text[:width] + (" [...]" if len(text) > width else "")))
    return out


def project_dir(agent):
    """Transcript folder. The five older agents write under ~/.claude/projects
    (their config dir is a symlink there); the newer ones under their own."""
    own = os.path.join(ROOT, "agents", agent, ".claude-config", "projects",
                       "-home-gabor-marveen-agents-" + agent)
    if os.path.isdir(own) and not os.path.islink(os.path.dirname(own)):
        return own
    return os.path.expanduser("~/.claude/projects/-home-gabor-marveen-agents-" + agent)


def window_tokens(agent, since):
    """This window's tokens, each API response counted once (message id)."""
    total, seen = 0, set()
    for f in glob.glob(project_dir(agent) + "/**/*.jsonl", recursive=True):
        try:
            if os.path.getmtime(f) < since:
                continue
            fh = open(f, errors="ignore")
        except OSError:
            continue
        with fh:
            for line in fh:
                if '"usage"' not in line:
                    continue
                try:
                    j = json.loads(line)
                except ValueError:
                    continue
                m = j.get("message") or {}
                u = m.get("usage")
                if not u:
                    continue
                key = (m.get("id"), j.get("requestId"))
                if key in seen:
                    continue
                seen.add(key)
                try:
                    ts = datetime.datetime.fromisoformat(j["timestamp"].replace("Z", "+00:00")).timestamp()
                except (KeyError, ValueError):
                    continue
                if ts >= since:
                    total += sum(u.get(k) or 0 for k in (
                        "input_tokens", "output_tokens", "cache_read_input_tokens",
                        "cache_creation_input_tokens"))
    return total


def running_agents():
    d = api("GET", "/api/agents")
    d = d if isinstance(d, list) else (d or {}).get("agents", [])
    return sorted(a.get("name") for a in d
                  if a.get("name") and a.get("name") != MAIN and a.get("running") is True)


def pick_reviewer(author, candidates, load):
    pool = [a for a in candidates if a != (author or "").lower()]
    if not pool:
        return None
    return min(pool, key=lambda a: (load.get(a, 0), a))


def brief(ev, reviewer):
    _eid, card_id, actor, _at, seq, title, assignee, desc, status = ev
    desc = " ".join(desc.split())
    if len(desc) > 1500:
        desc = desc[:1500] + " [...]"
    author = assignee or actor or "ismeretlen"
    lines = [
        "[Kereszt-ellenorzes] Nezd at ezt a KESZ kartyat. A gazda dontese (Telegram msg 8291): "
        "a kesz munkat egy masik ugynok ellenorzi, hogy kevesebb hiba jusson el hozza.",
        "",
        "Kartya: #%s (%s) -- %s" % (seq, card_id, title),
        "Szerzo: %s | statusz most: %s" % (author, status),
        "Leiras: %s" % (desc or "(ures)"),
        "Utolso kommentek (a tobbi: GET /api/kanban/%s/comments):" % card_id,
    ]
    lines += last_comments(card_id) or ["- (nincs komment)"]
    lines += [
        "",
        "Mit nezz meg (csak olvasol, a szerzo munkajat NEM javitod at):",
        "1. Azt csinalta-e, amit a kartya kert? Ami hianyzik, nevezd meg.",
        "2. Van-e bizonyitek (teszt-kimenet, meres, forras, commit, fajl), es MAGAD visszanezted-e? "
        "Amit nem tudtal visszanezni, ird oda, hogy nem nezted.",
        "3. A szamok, datumok, nevek stimmelnek-e a forrassal?",
        "Ha nincs mit atnezni (nincs kezzelfoghato eredmeny), az is valasz: egy sor.",
        "",
        "Eredmeny, ket lepes:",
        "a) Komment a kartyara: POST /api/kanban/%s/comments, author=%s, a szoveg ELEJE "
        "\"[Kereszt-ellenorzes] RENDBEN\" vagy \"[Kereszt-ellenorzes] HIBA:\" + a tetelek. "
        "A payloadot fajlbol kuldd (--data-binary @fajl)." % (card_id, reviewer),
        "b) HIBA eseten szolj a szerzonek: bash scripts/agent-msg.sh %s %s \"...\" "
        "(ha a szerzo nem fut, vagy a gazda, akkor marveen-nek)." % (reviewer, author),
        "Vegul zard le EZT az uzenetet notify:false-szal (ne menjen nyugta).",
    ]
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    st = load_state()
    if "last_event_id" not in st:
        st = {"last_event_id": max_event_id(), "assigned": []}
        if not a.dry_run:
            save_state(st)
        print("baseline: last_event_id=%s (backlog not dispatched)" % st["last_event_id"])
        return 0

    evs = done_events(st["last_event_id"])
    if not evs:
        return 0

    try:
        usage = json.load(open(os.path.join(ROOT, "store", "usage-latest.json")))
        reset = usage["claude"]["windows"]["seven_day"]["resets_at"]
        since = reset - 7 * 86400
    except (OSError, ValueError, KeyError, TypeError):
        since = time.time() - 7 * 86400
    candidates = running_agents()
    today = datetime.date.today().isoformat()
    load = {ag: window_tokens(ag, since) for ag in candidates}
    for rec in st.get("assigned", []):
        if rec.get("day") == today and rec.get("reviewer") in load:
            load[rec["reviewer"]] += REVIEW_WEIGHT

    failed = 0
    for ev in evs:
        eid, card_id, actor, _at, seq, title, assignee = ev[:7]
        author = (assignee or actor or "").strip()
        if author.lower() in OWNER_NAMES:
            st["last_event_id"] = eid
            continue
        reviewer = pick_reviewer(author.lower(), candidates, load)
        if not reviewer:
            print("no reviewer for #%s, will retry" % seq, file=sys.stderr)
            break
        text = brief(ev, reviewer)
        if a.dry_run:
            print("=== #%s -> %s\n%s\n" % (seq, reviewer, text))
            load[reviewer] = load.get(reviewer, 0) + REVIEW_WEIGHT
            continue
        try:
            r = api("POST", "/api/messages", {"from": MAIN, "to": reviewer, "content": text})
            mid = (r or {}).get("id")
        except Exception as e:  # noqa: BLE001 -- any failure means: not sent
            mid, err = None, e
        if not mid:
            # Stop here: the event is retried on the next run instead of being lost.
            print("send FAILED for #%s -> %s" % (seq, reviewer), file=sys.stderr)
            failed = 1
            break
        load[reviewer] = load.get(reviewer, 0) + REVIEW_WEIGHT
        st["last_event_id"] = eid
        st.setdefault("assigned", []).append({
            "day": today, "event": eid, "card": card_id, "seq": seq,
            "author": author, "reviewer": reviewer, "msg_id": mid})
        print("#%s (%s) -> %s, msg %s" % (seq, author, reviewer, mid))
    st["assigned"] = st.get("assigned", [])[-500:]
    if not a.dry_run:
        save_state(st)
    return failed


if __name__ == "__main__":
    sys.exit(main())
