#!/usr/bin/env python3
"""
ClaudeClaw fleet helper - shared, deterministic plumbing so agents don't burn
tokens hand-rolling curl/SQL/escaping in the model.

Covers: dashboard API auth (token always read from store/.dashboard-token, never
hardcoded), memory save/search, daily log, inter-agent messages, agent list,
kanban read helpers, the "Rád vár" label toggle (kanban-rad-var), wait reasons
(kanban-var), and
Telegram MarkdownV2 escaping.

Importable as a module or used from the CLI. See README.md for usage.

Config (no hardcoded paths or secrets):
  CLAW_DIR  - project root (the dir containing `store/`). If unset, the project
              root is auto-detected by walking up from the current directory
              until a `store/.dashboard-token` is found.
  CLAW_BASE - dashboard base url (default http://localhost:3420).
"""
import json
import os
import sys
import sqlite3
import urllib.request
import urllib.error


def project_dir():
    env = os.environ.get("CLAW_DIR")
    if env and os.path.isdir(os.path.join(env, "store")):
        return env
    d = os.getcwd()
    while True:
        if os.path.isfile(os.path.join(d, "store", ".dashboard-token")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    raise RuntimeError("project root not found (set CLAW_DIR to the dir containing store/)")


def base_url():
    return os.environ.get("CLAW_BASE", "http://localhost:3420").rstrip("/")


def token():
    with open(os.path.join(project_dir(), "store", ".dashboard-token")) as f:
        return f.read().strip()


def db_path():
    return os.path.join(project_dir(), "store", "claudeclaw.db")


def api(method, path, payload=None, timeout=20, want_headers=False):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(base_url() + path, data=data, method=method)
    req.add_header("Authorization", "Bearer " + token())
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode()
            headers = dict(r.headers.items())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"API {method} {path} -> {e.code}: {e.read().decode()[:200]}")
    try:
        parsed = json.loads(body)
    except ValueError:
        parsed = body
    return (parsed, headers) if want_headers else parsed


def save_memory(agent, content, category="warm", keywords=""):
    return api("POST", "/api/memories", {"agent_id": agent, "content": content,
                                         "category": category, "keywords": keywords})


# MEMKERESVAK917: this used to return the body and drop r.headers on the floor.
# The memory search is deliberately forgiving -- when nothing matches the query
# as asked, it answers with whatever the leftover filler words pulled in, and
# that answer is byte-identical to a real hit in the BODY. The only thing that
# tells them apart is the X-Memory-Search response header. An agent calling this
# helper could not opt in: there is no `-D` to add to a Python function.
#
# So the label comes back WITH the rows, and `relaxed` gets its own warning
# string, because the whole failure mode is a caller who skims the rows.
def search_memory(agent, q, category=None, strict=False):
    from urllib.parse import quote
    path = f"/api/memories?agent={quote(agent)}&q={quote(q)}"
    if category:
        # No limit widening here on purpose. Until #1384 the tier filter ran
        # AFTER the limit, so a category search truncated in silence and this
        # asked for limit=200 to work around it. #1384 pushed the filter into
        # the search SQL, so the default window now holds rows the caller
        # actually asked for, and a hardcoded 200 would just be a bigger page
        # than every other call site uses.
        path += f"&category={quote(category)}"
    if strict:
        path += "&strict=1"
    rows, headers = api("GET", path, want_headers=True)
    label = ""
    for k, v in headers.items():
        if k.lower() == "x-memory-search":
            label = v
            break
    relaxed = "relaxed=true" in label
    out = {
        "label": label,
        "relaxed": relaxed,
        "strict": strict,
        "hits": len(rows) if isinstance(rows, list) else None,
        "rows": rows,
    }
    if relaxed:
        out["warning"] = ("relaxed=true -- semmi nem illeszkedett UGY, AHOGY KERTED. "
                          "Ezek mentett kozelitesek, NEM bizonyitek. Hiany-allitashoz "
                          "futtasd ujra strict=1-gyel.")
    return out


def daily_log(agent, content):
    return api("POST", "/api/daily-log", {"agent_id": agent, "content": content})


def send_message(from_agent, to_agent, content):
    return api("POST", "/api/messages", {"from": from_agent, "to": to_agent, "content": content})


def list_agents():
    return api("GET", "/api/agents")


def _kanban(where, params=()):
    con = sqlite3.connect(db_path())
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT id, title, status, assignee, priority, project, due_date, "
            "updated_at FROM kanban_cards WHERE archived_at IS NULL AND " + where,
            params).fetchall()
    finally:
        con.close()
    return [dict(r) for r in rows]


def kanban_due_today():
    return _kanban(
        "due_date IS NOT NULL AND status != 'done' "
        "AND date(due_date,'unixepoch','localtime') <= date('now','localtime') "
        "ORDER BY due_date")


def kanban_stuck(idle_seconds=14400):
    return _kanban("status = 'in_progress' AND updated_at < strftime('%s','now') - ? "
                   "ORDER BY updated_at", (idle_seconds,))


def kanban_by_status(status):
    return _kanban("status = ? ORDER BY priority DESC, updated_at DESC", (status,))


# The "Rád vár" label puts an agent's card into the owner's queue strip on the
# dashboard (web/app.js, KANBAN_OWNER_QUEUE_LABEL) while the card stays with
# the agent. Put it on when you ask the owner for a decision, take it off when
# he has answered. Matched by name with accents and case folded, the same way
# the dashboard matches it, so both sides agree on which label counts.
OWNER_QUEUE_LABEL = "Rád vár"


def _fold(s):
    import unicodedata
    return "".join(ch for ch in unicodedata.normalize("NFD", str(s or ""))
                   if not unicodedata.combining(ch)).strip().lower()


def kanban_owner_queue(ref, remove=False):
    """Put the "Rád vár" label on a card (or take it off with remove=True).
    `ref` is the card number as the owner sees it (427, #427) or its hex id.
    Goes through the dashboard API, never the database. Idempotent: a second
    call reports changed=False instead of failing."""
    ref = str(ref).strip().lstrip("#")
    cards = api("GET", "/api/kanban")
    if ref.isdigit():
        hit = [c for c in cards if c.get("seq") == int(ref)]
    else:
        hit = [c for c in cards if c.get("id") == ref]
    if not hit:
        raise RuntimeError(f"no card {ref!r} among the non-archived cards")
    card = hit[0]
    want = _fold(OWNER_QUEUE_LABEL)
    label = next((l for l in api("GET", "/api/kanban/labels") if _fold(l.get("name")) == want), None)
    if label is None:
        raise RuntimeError(f"label {OWNER_QUEUE_LABEL!r} does not exist (GET /api/kanban/labels)")
    has = any(l.get("id") == label["id"] for l in card.get("labels") or [])
    changed = False
    if remove and has:
        api("DELETE", f"/api/kanban/{card['id']}/labels/{label['id']}")
        changed = True
    elif not remove and not has:
        api("POST", f"/api/kanban/{card['id']}/labels", {"labelId": label["id"]})
        changed = True
    after = {l.get("id") for l in api("GET", f"/api/kanban/{card['id']}/labels")}
    return {"seq": card.get("seq"), "id": card["id"], "title": card.get("title"),
            "assignee": card.get("assignee"), "label": label["name"],
            "on_owner_strip": label["id"] in after, "changed": changed}


def _resolve_kanban_card(ref, cards):
    ref = str(ref).strip().lstrip("#")
    if ref.isdigit():
        hit = [c for c in cards if c.get("seq") == int(ref)]
    else:
        hit = [c for c in cards if str(c.get("id", "")).startswith(ref)]
    if len(hit) != 1:
        raise RuntimeError(f"no unique card {ref!r} among the non-archived cards")
    return hit[0]


def kanban_wait_reason(ref, reason=None, clear=False):
    cards = api("GET", "/api/kanban")
    card = _resolve_kanban_card(ref, cards)
    path = f"/api/kanban/{card['id']}"
    if clear:
        api("DELETE", path + "/wait")
    elif card.get("status") == "waiting":
        api("PUT", path + "/wait", reason)
    else:
        payload = {"status": "waiting", "sort_order": 0, "wait": reason}
        agent = os.environ.get("FLEET_AGENT_NAME", "").strip()
        if agent:
            payload["actor"] = agent
        api("POST", path + "/move", payload)
    refreshed = _resolve_kanban_card(card["id"], api("GET", "/api/kanban"))
    return {key: refreshed.get(key) for key in
            ("seq", "id", "title", "status", "wait_kind", "wait_note", "wait_until")}


_MDV2_SPECIAL = r"_*[]()~`>#+-=|{}.!\\"


def escape_mdv2(text):
    """Escape literal text for Telegram MarkdownV2. Escape your dynamic text with
    this, THEN wrap intended formatting (e.g. '*'+escape_mdv2(label)+'*' for bold)."""
    return "".join("\\" + ch if ch in _MDV2_SPECIAL else ch for ch in str(text))


def escape_mdv2_keep_bold(text):
    """Escape for MarkdownV2 but leave '*' untouched, so hand-authored '*bold*'
    markers survive. Use this for long, hand-written messages (e.g. the morning
    brief) where the formatting is inline in the prose rather than wrapped around
    programmatic labels. Only '*' is preserved: every other special is escaped,
    so pair your asterisks or Telegram rejects the message with a 400."""
    return "".join(
        ch if ch == "*" else ("\\" + ch if ch in _MDV2_SPECIAL else ch)
        for ch in str(text)
    )


def outgoing_gate_check(text):
    """Return a list of problems the outgoing-copy-gate hook would reject.
    Cheaper to run here than to have the send blocked."""
    problems = []
    if "\u2014" in text or "\u2013" in text:
        problems.append("em/en dash present (the gate rejects it)")
    if " -- " in text.replace("\\-", "-"):
        problems.append("space-hyphen-hyphen-space present (the gate rejects it)")
    stars = text.count("*") - text.count("\\*")
    if stars % 2:
        problems.append("odd number of unescaped '*' (Telegram 400: unclosed entity)")
    return problems


def _out(v):
    print(json.dumps(v, ensure_ascii=False, indent=2) if isinstance(v, (dict, list)) else v)


def main(argv):
    if not argv:
        print(__doc__)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd == "mdv2":
        print(escape_mdv2(rest[0] if rest else sys.stdin.read()))
    elif cmd == "mdv2b":
        src = rest[0] if rest else sys.stdin.read()
        bad = outgoing_gate_check(src)
        if bad:
            sys.stderr.write("BLOCKED before send:\n- " + "\n- ".join(bad) + "\n")
            return 2
        print(escape_mdv2_keep_bold(src))
    elif cmd == "mem-save":
        _out(save_memory(rest[0], rest[1], rest[2] if len(rest) > 2 else "warm",
                         rest[3] if len(rest) > 3 else ""))
    elif cmd == "mem-search":
        res = search_memory(rest[0], rest[1], rest[2] if len(rest) > 2 else None,
                            strict=(len(rest) > 3 and rest[3] in ("strict", "1", "true")))
        # stderr as well as the JSON field: the rows are what a reader's eye
        # goes to, and a rescued near-miss looks exactly like a real hit there.
        if res.get("warning"):
            sys.stderr.write("FIGYELEM: " + res["warning"] + "\n")
        _out(res)
    elif cmd == "daily-log":
        _out(daily_log(rest[0], rest[1]))
    elif cmd == "msg":
        _out(send_message(rest[0], rest[1], rest[2]))
    elif cmd == "agents":
        _out([{"name": a.get("name"), "running": a.get("running"),
               "model": a.get("model")} for a in list_agents()])
    elif cmd == "kanban-due":
        _out(kanban_due_today())
    elif cmd == "kanban-stuck":
        _out(kanban_stuck(int(rest[0]) if rest else 14400))
    elif cmd == "kanban-status":
        _out(kanban_by_status(rest[0]))
    elif cmd == "kanban-rad-var":
        args = [a for a in rest if a != "--le"]
        if len(args) != 1:
            sys.stderr.write("usage: fleet.py kanban-rad-var <seq|id> [--le]\n")
            return 2
        _out(kanban_owner_queue(args[0], remove="--le" in rest))
    elif cmd == "kanban-var":
        usage = ("usage: fleet.py kanban-var <seq|id> "
                 "(gazda|kulso <ki>|kartya <seq|id>|datum YYYY-MM-DD) "
                 "[--megj SZOVEG] | kanban-var <seq|id> --torol\n")
        if len(rest) == 2 and rest[1] == "--torol":
            _out(kanban_wait_reason(rest[0], clear=True))
        else:
            try:
                note = None
                args = list(rest)
                if "--megj" in args:
                    pos = args.index("--megj")
                    if pos + 1 >= len(args) or pos + 2 != len(args):
                        raise ValueError
                    note = args[pos + 1]
                    args = args[:pos]
                if len(args) < 2:
                    raise ValueError
                card_ref, kind = args[0], args[1].lower()
                aliases = {"gazda": "owner", "owner": "owner", "kulso": "external",
                           "external": "external", "kartya": "card", "card": "card",
                           "datum": "date", "date": "date"}
                kind = aliases[kind]
                reason = {"kind": kind}
                if note:
                    reason["note"] = note
                if kind == "owner" and len(args) != 2:
                    raise ValueError
                if kind == "external":
                    if len(args) != 3 or note:
                        raise ValueError
                    reason["note"] = args[2]
                elif kind == "card":
                    if len(args) != 3:
                        raise ValueError
                    reason["blockerId"] = _resolve_kanban_card(args[2], api("GET", "/api/kanban"))["id"]
                elif kind == "date":
                    if len(args) != 3:
                        raise ValueError
                    from datetime import datetime, time
                    from zoneinfo import ZoneInfo
                    day = datetime.strptime(args[2], "%Y-%m-%d").date()
                    reason["until"] = int(datetime.combine(day, time(23, 59, 59),
                                                           ZoneInfo("Europe/London")).timestamp())
                _out(kanban_wait_reason(card_ref, reason=reason))
            except (KeyError, ValueError):
                sys.stderr.write(usage)
                return 2
    else:
        sys.stderr.write(f"unknown command: {cmd}\n")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
